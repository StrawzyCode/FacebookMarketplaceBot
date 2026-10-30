import pytest

from marketplace_bot.parsing import (
    clean_page_text,
    extract_item_id,
    parse_card_text,
    parse_listed_line,
    parse_price,
    title_has_excluded_keyword,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$1,200", 1200.0),
        ("$45", 45.0),
        ("CA$1,050", 1050.0),
        ("A$ 300", 300.0),
        ("£2,499.99", 2499.99),
        ("1.200 €", 1200.0),
        ("1 200,50 €", 1200.5),
        ("Free", 0.0),
        ("FREE", 0.0),
        ("Austin, TX", None),
        ("120K miles", None),
        ("2015 Toyota Tacoma", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_extract_item_id():
    assert extract_item_id("https://www.facebook.com/marketplace/item/1234567890/?ref=search") == "1234567890"
    assert extract_item_id("/marketplace/item/42/") == "42"
    assert extract_item_id("https://www.facebook.com/marketplace/category/vehicles") is None


def test_parse_vehicle_card():
    card = "$18,500\n2016 Toyota Tacoma TRD Off-Road\nRound Rock, TX\n98K miles"
    assert parse_card_text(card) == {
        "price_text": "$18,500",
        "price": 18500.0,
        "title": "2016 Toyota Tacoma TRD Off-Road",
        "location": "Round Rock, TX",
        "extras": ["98K miles"],
    }


def test_parse_card_with_crossed_out_old_price():
    parsed = parse_card_text("$150\n$200\nUplift standing desk\nDenver, CO")
    assert parsed["price"] == 150.0
    assert parsed["title"] == "Uplift standing desk"
    assert parsed["location"] == "Denver, CO"
    assert parsed["extras"] == []


def test_parse_card_without_price_or_location():
    parsed = parse_card_text("Couch, must go this weekend")
    assert parsed["price"] is None
    assert parsed["title"] == "Couch, must go this weekend"


def test_parse_listed_line():
    text = "Some title\n$40\nListed 3 days ago in Austin, TX\nDetails"
    assert parse_listed_line(text) == ("3 days ago", "Austin, TX")
    assert parse_listed_line("nothing here") == ("", "")


def test_clean_page_text_strips_chrome_and_duplicates():
    text = "Message\nGreat desk\nSave\nShare\nGreat desk\nSee more\nBarely used, no wobble"
    assert clean_page_text(text) == "Great desk\nBarely used, no wobble"
    assert clean_page_text("x" * 100, max_chars=10).endswith("[...truncated]")


def test_title_keyword_exclusion_matches_whole_words():
    assert title_has_excluded_keyword("WANTED: road bike", ["wanted"]) == "wanted"
    assert title_has_excluded_keyword("iPhone 13 case only", ["case only"]) == "case only"
    assert title_has_excluded_keyword("Unwanted gift - road bike", ["wanted"]) is None
    assert title_has_excluded_keyword("Road bike", []) is None
