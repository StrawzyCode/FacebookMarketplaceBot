"""Drives a real Chromium browser through Facebook Marketplace using your own logged-in session.

Your login lives in a browser profile folder on your computer (``.fb_profile`` by
default). The bot never sees or stores your password.
"""

from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import quote, urlencode

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Page, sync_playwright

from .config import Settings
from .geo import KM_PER_MILE
from .models import Listing, SearchPlan
from .parsing import clean_page_text, extract_item_id, item_url, parse_card_text, parse_listed_line, parse_price

log = logging.getLogger(__name__)

MARKETPLACE = "https://www.facebook.com/marketplace"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 4_500_000

CARDS_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/marketplace/item/"]')) {
    const m = a.href.match(/\\/marketplace\\/item\\/(\\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const img = a.querySelector('img');
    out.push({ href: a.href, text: a.innerText || '', img: img ? (img.currentSrc || img.src || '') : '' });
  }
  return out;
}
"""

DETAIL_JS = """
() => {
  const main = document.querySelector('div[role="main"]') || document.body;
  const meta = (p) => (document.querySelector(`meta[property="${p}"]`) || {}).content || '';
  const h1 = main.querySelector('h1');
  const imgs = [];
  for (const img of main.querySelectorAll('img')) {
    const src = img.currentSrc || img.src || '';
    if (!src.startsWith('http')) continue;
    imgs.push({ src, w: img.naturalWidth || img.width || 0, h: img.naturalHeight || img.height || 0 });
  }
  return {
    text: main.innerText || '',
    title: h1 ? h1.innerText : '',
    ogTitle: meta('og:title'),
    ogDescription: meta('og:description'),
    ogImage: meta('og:image'),
    imgs,
  };
}
"""


class NotLoggedInError(RuntimeError):
    pass


def _days_param(days: int | None) -> int | None:
    """Marketplace only accepts 1, 7 or 30 for 'days since listed'."""
    if not days or days <= 0:
        return None
    for allowed in (1, 7, 30):
        if days <= allowed:
            return allowed
    return None


def build_search_url(query: str, plan: SearchPlan, location_id: str = "") -> str:
    base = f"{MARKETPLACE}/{quote(location_id, safe='')}/search/" if location_id else f"{MARKETPLACE}/search/"
    params: dict[str, str | int] = {"query": query, "exact": "false"}
    if plan.min_price is not None:
        params["minPrice"] = plan.min_price
    if plan.max_price is not None:
        params["maxPrice"] = plan.max_price
    days = _days_param(plan.days_since_listed)
    if days:
        params["daysSinceListed"] = days
    if plan.radius_miles:
        # Marketplace's radius is in km. Facebook may ignore it and use the radius saved on
        # your account instead; the bot's own distance filter enforces your radius either way.
        params["radius"] = max(1, round(plan.radius_miles * KM_PER_MILE))
    return f"{base}?{urlencode(params)}"


def pick_listing_images(imgs: list[dict], og_image: str, limit: int) -> list[str]:
    """Choose the listing's photos from all <img> tags on the page, largest first."""
    candidates = []
    for img in imgs:
        src = img.get("src", "")
        if "fbcdn" not in src and "scontent" not in src:
            continue
        w, h = img.get("w", 0) or 0, img.get("h", 0) or 0
        if max(w, h) < 120:  # avatars, icons, tiny thumbnails
            continue
        candidates.append((w * h, src))
    candidates.sort(key=lambda c: c[0], reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for src in ([og_image] if og_image else []) + [src for _, src in candidates]:
        key = src.split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(src)
        if len(out) >= limit:
            break
    return out


class MarketplaceScraper:
    def __init__(self, settings: Settings, headless: bool | None = None):
        self.settings = settings
        self.headless = settings.headless if headless is None else headless
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> MarketplaceScraper:
        self._pw = sync_playwright().start()
        self.context = launch_context(self._pw, self.settings, self.headless)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _pause(self, scale: float = 1.0) -> None:
        time.sleep(random.uniform(self.settings.min_delay_seconds, self.settings.max_delay_seconds) * scale)

    def _check_login(self) -> None:
        if "/login" in self.page.url or "checkpoint" in self.page.url or not is_logged_in(self.context):
            raise NotLoggedInError(
                "Facebook isn't logged in for the bot. Run:  python -m marketplace_bot login"
            )

    def ensure_logged_in(self) -> None:
        self.page.goto(f"{MARKETPLACE}/", wait_until="domcontentloaded")
        self._pause(0.5)
        self._check_login()

    def search(self, query: str, plan: SearchPlan) -> list[Listing]:
        url = build_search_url(query, plan, self.settings.fb_location_id)
        log.info("Searching: %s", url)
        self.page.goto(url, wait_until="domcontentloaded")
        self._check_login()
        try:
            self.page.wait_for_selector('a[href*="/marketplace/item/"]', timeout=15000)
        except PlaywrightError:
            log.info("No results for %r", query)
            return []

        found: dict[str, Listing] = {}
        stale_rounds = 0
        while len(found) < self.settings.max_results_per_query and stale_rounds < 3:
            before = len(found)
            for card in self.page.evaluate(CARDS_JS):
                listing = card_to_listing(card)
                if listing and listing.id not in found:
                    found[listing.id] = listing
            stale_rounds = stale_rounds + 1 if len(found) == before else 0
            self.page.mouse.wheel(0, random.randint(1800, 3000))
            self._pause(0.6)
        listings = list(found.values())[: self.settings.max_results_per_query]
        for listing in listings:
            listing.found_by.append(query)
        return listings

    def fetch_details(self, listing: Listing) -> None:
        self.page.goto(listing.url, wait_until="domcontentloaded")
        self._check_login()
        try:
            self.page.wait_for_selector('div[role="main"] h1, div[role="main"] span', timeout=15000)
        except PlaywrightError:
            pass
        self._expand_description()
        data = self.page.evaluate(DETAIL_JS)
        apply_details(listing, data, self.settings.images_per_listing)
        self._pause()

    def _expand_description(self) -> None:
        try:
            more = self.page.locator('div[role="main"] [role="button"]', has_text=re.compile(r"^\s*See more\s*$"))
            if more.count():
                more.first.click(timeout=2000)
                self.page.wait_for_timeout(500)
        except PlaywrightError:
            pass

    def download_images(self, urls: list[str]) -> list[tuple[str, bytes]]:
        images: list[tuple[str, bytes]] = []
        for url in urls:
            try:
                resp = self.context.request.get(url, timeout=15000)
            except PlaywrightError as exc:
                log.debug("Image download failed for %s: %s", url, exc)
                continue
            media_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not resp.ok or media_type not in ALLOWED_IMAGE_TYPES:
                continue
            body = resp.body()
            if 0 < len(body) <= MAX_IMAGE_BYTES:
                images.append((media_type, body))
        return images


def card_to_listing(card: dict) -> Listing | None:
    item_id = extract_item_id(card.get("href", ""))
    if not item_id:
        return None
    parsed = parse_card_text(card.get("text", ""))
    return Listing(
        id=item_id,
        url=item_url(item_id),
        title=parsed["title"],
        price=parsed["price"],
        price_text=parsed["price_text"],
        location=parsed["location"],
        extras=parsed["extras"],
        image_url=card.get("img", ""),
    )


def apply_details(listing: Listing, data: dict, image_limit: int) -> None:
    """Merge what was read off a listing page into the listing."""
    text = data.get("text", "")
    title = (data.get("title") or data.get("ogTitle") or "").strip()
    if title:
        listing.title = title
    listing.description = (data.get("ogDescription") or "").strip()
    listing.page_text = clean_page_text(text)
    ago, loc = parse_listed_line(text)
    listing.listed_ago = ago or listing.listed_ago
    if loc and not listing.location:
        listing.location = loc
    if listing.price is None:
        for line in text.splitlines()[:40]:
            price = parse_price(line)
            if price is not None:
                listing.price, listing.price_text = price, line.strip()
                break
    listing.image_urls = pick_listing_images(data.get("imgs", []), data.get("ogImage", ""), image_limit)
    if not listing.image_url and listing.image_urls:
        listing.image_url = listing.image_urls[0]


def is_logged_in(context: BrowserContext) -> bool:
    return any(c["name"] == "c_user" for c in context.cookies("https://www.facebook.com"))


def launch_context(pw, settings: Settings, headless: bool) -> BrowserContext:
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "headless": headless,
        "viewport": {"width": 1366, "height": 900},
        "locale": "en-US",
    }
    if settings.browser_channel:
        kwargs["channel"] = settings.browser_channel
    if settings.chromium_executable:
        kwargs["executable_path"] = settings.chromium_executable
    return pw.chromium.launch_persistent_context(str(settings.profile_dir), **kwargs)


def interactive_login(settings: Settings, timeout_minutes: int = 10) -> bool:
    """Open a visible browser so you can log in to Facebook yourself; the session is kept for later runs."""
    with sync_playwright() as pw:
        context = launch_context(pw, settings, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        if is_logged_in(context):
            print("Already logged in to Facebook. You're all set.")
            context.close()
            return True
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        print("A browser window opened. Log in to Facebook there (including any 2FA step).")
        print(f"Waiting up to {timeout_minutes} minutes...")
        deadline = time.time() + timeout_minutes * 60
        while time.time() < deadline:
            if is_logged_in(context) and "/login" not in page.url and "checkpoint" not in page.url:
                page.wait_for_timeout(3000)  # let Facebook finish setting cookies
                print("Logged in. Your session is saved in", settings.profile_dir)
                context.close()
                return True
            page.wait_for_timeout(2000)
        context.close()
        print("Timed out waiting for login.")
        return False
