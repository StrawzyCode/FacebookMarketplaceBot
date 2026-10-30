import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from marketplace_bot.scraper import (
    CARDS_JS,
    DETAIL_JS,
    apply_details,
    build_search_url,
    card_to_listing,
    pick_listing_images,
)

from .conftest import make_listing


def test_search_url_carries_filters(plan):
    plan.min_price = 50
    plan.days_since_listed = 5
    url = build_search_url("standing desk", plan, "112111905481230")
    parsed = urlparse(url)
    assert parsed.path == "/marketplace/112111905481230/search/"
    assert parse_qs(parsed.query) == {
        "query": ["standing desk"],
        "exact": ["false"],
        "minPrice": ["50"],
        "maxPrice": ["300"],
        "daysSinceListed": ["7"],
        "radius": ["48"],
    }


def test_search_url_without_location_uses_account_location(plan):
    plan.radius_miles = None
    plan.max_price = None
    url = build_search_url("desk", plan)
    assert url == "https://www.facebook.com/marketplace/search/?query=desk&exact=false"


def test_pick_listing_images_prefers_large_photos_and_skips_avatars():
    imgs = [
        {"src": "https://scontent.fbcdn.net/avatar.jpg?x=1", "w": 40, "h": 40},
        {"src": "https://scontent.fbcdn.net/small.jpg", "w": 300, "h": 200},
        {"src": "https://scontent.fbcdn.net/big.jpg?sig=2", "w": 960, "h": 720},
        {"src": "https://static.xx.fbcdn.net/rsrc.php/icon.png", "w": 20, "h": 20},
        {"src": "https://example.com/ad.jpg", "w": 900, "h": 900},
    ]
    picked = pick_listing_images(imgs, "https://scontent.fbcdn.net/big.jpg?sig=1", limit=3)
    assert picked == ["https://scontent.fbcdn.net/big.jpg?sig=1", "https://scontent.fbcdn.net/small.jpg"]


def test_apply_details_fills_missing_fields():
    listing = make_listing("7", "Desk", None, "")
    data = {
        "text": "Electric Desk\n$180\nListed 2 days ago in Round Rock, TX\nDetails\nCondition\nUsed - like new",
        "title": "Electric Desk",
        "ogDescription": "Works great, 3 memory presets.",
        "ogImage": "https://scontent.fbcdn.net/p.jpg",
        "imgs": [],
    }
    apply_details(listing, data, image_limit=3)
    assert listing.title == "Electric Desk"
    assert listing.price == 180 and listing.price_text == "$180"
    assert listing.location == "Round Rock, TX"
    assert listing.listed_ago == "2 days ago"
    assert listing.description == "Works great, 3 memory presets."
    assert "Used - like new" in listing.page_text
    assert listing.image_url == "https://scontent.fbcdn.net/p.jpg"


def _browser_path() -> str | None:
    for candidate in (os.environ.get("CHROMIUM_EXECUTABLE"), "/opt/pw-browsers/chromium"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


SEARCH_PAGE = """
<div role="main">
  <a href="/marketplace/item/111/?ref=search"><img src="https://scontent.fbcdn.net/1.jpg">
    <div><span>$120</span></div><div><span>Electric standing desk</span></div><div><span>Austin, TX</span></div></a>
  <a href="/marketplace/item/111/?ref=search">duplicate link to the same item</a>
  <a href="/marketplace/item/222/"><div>$90</div><div>$110</div><div>Sit stand desk frame</div><div>Round Rock, TX</div></a>
  <a href="/marketplace/category/furniture">Furniture</a>
</div>
"""

DETAIL_PAGE = """
<html><head><meta property="og:description" content="Great desk, no wobble."></head>
<body><div role="banner">Facebook</div><div role="main">
  <h1>Uplift V2 standing desk</h1><div>$300</div><div>Listed 1 week ago in Austin, TX</div>
  <img src="https://scontent.fbcdn.net/main.jpg" width="800" height="600">
</div></body></html>
"""


@pytest.mark.skipif(_browser_path() is None, reason="no Chromium available")
def test_page_scripts_extract_cards_and_details():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_browser_path())
        page = browser.new_page()
        page.route("https://www.facebook.com/**", lambda route: route.fulfill(content_type="text/html", body=SEARCH_PAGE))
        page.goto("https://www.facebook.com/marketplace/search/?query=desk")
        cards = page.evaluate(CARDS_JS)
        listings = [card_to_listing(c) for c in cards]

        page.set_content(DETAIL_PAGE)
        detail = page.evaluate(DETAIL_JS)
        browser.close()

    assert [l.id for l in listings] == ["111", "222"]
    assert (listings[0].title, listings[0].price, listings[0].location) == ("Electric standing desk", 120, "Austin, TX")
    assert listings[0].image_url == "https://scontent.fbcdn.net/1.jpg"
    assert (listings[1].price, listings[1].title) == (90, "Sit stand desk frame")
    assert detail["title"] == "Uplift V2 standing desk"
    assert detail["ogDescription"] == "Great desk, no wobble."
    assert "Listed 1 week ago in Austin, TX" in detail["text"]
    assert "Facebook" not in detail["text"]
