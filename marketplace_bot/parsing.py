"""Pure text-parsing helpers for Marketplace pages (no browser needed, easy to test)."""

from __future__ import annotations

import re

_CURRENCY = r"(?:[A-Z]{1,3}\s?)?[$€£¥₹₩₱]"
_NUMBER = r"\d[\d,.   ]*\d|\d"
PRICE_RE = re.compile(
    rf"^\s*(?:{_CURRENCY}\s?(?P<a>{_NUMBER})|(?P<b>{_NUMBER})\s?(?:€|£|kr|zł|Kč|Ft|lei|CHF))\s*$"
)
FREE_RE = re.compile(r"^\s*free\s*$", re.IGNORECASE)
ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")
LISTED_RE = re.compile(r"Listed\s+(?P<ago>.+?)\s+in\s+(?P<loc>[^\n]+)", re.IGNORECASE)
# "Austin, TX", "St. Louis, MO", "Toronto, ON", "Saint-Jérôme, QC"
LOCATION_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ0-9 .'\-]{2,}, ?[A-Za-zÀ-ÖØ-öø-ÿ .'\-]{2,}$")

# Lines of Facebook chrome that carry no information about the item.
_BOILERPLATE = {
    "message", "send", "save", "share", "send seller a message", "hi, is this available?",
    "is this still available?", "see more", "see less", "today's picks", "marketplace",
    "notifications", "menu", "home", "watch", "groups", "gaming", "see translation",
    "seller information", "seller details", "seller's description", "location is approximate",
    "sponsored", "more", "follow", "report", "close",
}


def parse_number(raw: str) -> float | None:
    """Parse '1,200', '1.200', '1 200,50', '12.5' into a float."""
    s = re.sub(r"[\s  ]", "", raw)
    if not s:
        return None
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        s = s.replace(thousands, "").replace(decimal, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        tail = s.rsplit(sep, 1)[1]
        if s.count(sep) == 1 and len(tail) != 3:
            s = s.replace(sep, ".")
        else:
            s = s.replace(sep, "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_price(text: str) -> float | None:
    """Return the price in a single line of text, 0 for 'Free', or None if it isn't a price."""
    if FREE_RE.match(text):
        return 0.0
    m = PRICE_RE.match(text)
    if not m:
        return None
    return parse_number(m.group("a") or m.group("b"))


def is_price_line(line: str) -> bool:
    return parse_price(line) is not None


def extract_item_id(href: str) -> str | None:
    m = ITEM_ID_RE.search(href or "")
    return m.group(1) if m else None


def item_url(item_id: str) -> str:
    return f"https://www.facebook.com/marketplace/item/{item_id}/"


def looks_like_location(line: str) -> bool:
    return bool(LOCATION_RE.match(line.strip())) and not is_price_line(line)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def parse_card_text(text: str) -> dict:
    """Split a search-result card's text into price, title, location and extra lines.

    Cards read top to bottom roughly as: price (sometimes followed by the old,
    crossed-out price), title, location, then optional extras such as mileage.
    """
    lines = _lines(text)
    price_text = ""
    price = None
    rest: list[str] = []
    for line in lines:
        if price is None and is_price_line(line):
            price_text, price = line, parse_price(line)
        elif price is not None and not rest and is_price_line(line):
            continue  # old/crossed-out price right after the current one
        else:
            rest.append(line)

    title = ""
    location = ""
    extras: list[str] = []
    for line in rest:
        if not title and not looks_like_location(line):
            title = line
        elif not location and looks_like_location(line):
            location = line
        else:
            extras.append(line)
    if not title and rest:
        title = rest[0]
        if location == title:
            location = ""
    return {"price_text": price_text, "price": price, "title": title, "location": location, "extras": extras}


def parse_listed_line(text: str) -> tuple[str, str]:
    """From 'Listed 3 days ago in Austin, TX' return ('3 days ago', 'Austin, TX')."""
    m = LISTED_RE.search(text or "")
    if not m:
        return "", ""
    return m.group("ago").strip(), m.group("loc").strip()


def clean_page_text(text: str, max_chars: int = 6000) -> str:
    """Strip Facebook UI chrome and duplicate lines from a listing page's text."""
    out: list[str] = []
    seen: set[str] = set()
    for line in _lines(text):
        key = line.lower()
        if key in _BOILERPLATE or key in seen:
            continue
        seen.add(key)
        out.append(line)
    cleaned = "\n".join(out)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n[...truncated]"
    return cleaned


def title_has_excluded_keyword(title: str, keywords: list[str]) -> str | None:
    lowered = f" {title.lower()} "
    for kw in keywords:
        kw = kw.strip().lower()
        if kw and re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", lowered):
            return kw
    return None
