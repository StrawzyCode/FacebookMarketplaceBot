import pytest

from marketplace_bot.rules import ListingText, RulesMatcher, check_requirement, parse_prompt

from .conftest import make_listing


def test_parses_a_typical_prompt():
    plan = parse_prompt(
        "Electric standing desk, at least 60 inches wide, no wobble, under $250, within 25 miles of Denver, CO"
    )
    assert plan.location == "Denver, CO"
    assert plan.radius_miles == 25
    assert plan.max_price == 250 and plan.min_price is None
    assert plan.queries[:2] == ["electric standing desk", "standing desk"]
    assert plan.must_haves == ["at least 60 inches wide"]
    assert plan.deal_breakers == ["wobble"]


def test_parses_a_vehicle_prompt_without_confusing_mileage_for_price():
    plan = parse_prompt(
        "2016-2019 Toyota Tacoma 4x4, under 120k miles, clean title, no rust, under $28,000, within 75 miles of 78701"
    )
    assert plan.location == "78701"
    assert plan.radius_miles == 75
    assert plan.max_price == 28000
    assert plan.queries[0] == "toyota tacoma 4x4"
    assert plan.must_haves == ["year 2016-2019", "under 120k miles", "clean title"]
    assert plan.deal_breakers == ["rust"]


@pytest.mark.parametrize(
    "prompt, location, radius, low, high, days",
    [
        ("used PS5 near Austin TX, $300-$400, posted in the last week", "Austin TX", None, 300, 400, 7),
        ("sectional couch within 20 miles of austin, tx under 500", "austin, tx", 20, None, 500, None),
        ("fridge with ice maker in Houston, TX budget $600", "Houston, TX", None, None, 600, None),
        ("bike within 10 km of Toronto, ON, $1.5k max", "Toronto, ON", pytest.approx(6.21, abs=0.01), None, 1500, None),
        ("Mountain bike under $400 near me", "", None, None, 400, None),
        ("Dining table between 200 and 350, listed today", "", None, 200, 350, 1),
    ],
)
def test_location_price_and_age(prompt, location, radius, low, high, days):
    plan = parse_prompt(prompt)
    assert (plan.location, plan.radius_miles, plan.min_price, plan.max_price, plan.days_since_listed) == (
        location, radius, low, high, days
    )


def test_defaults_fill_in_missing_location_and_radius():
    plan = parse_prompt("Mountain bike under $400", default_location="Austin, TX", default_radius=30)
    assert (plan.location, plan.radius_miles) == ("Austin, TX", 30)


def test_structured_prompt():
    plan = parse_prompt(
        "item: road bike\nlocation: Portland, OR\ndistance: 30 miles\nbudget: 800\n"
        "must have: carbon frame, 56cm\nnice to have: Shimano 105\navoid: crashed, cracked\nsearch: cervelo, trek emonda"
    )
    assert plan.location == "Portland, OR" and plan.radius_miles == 30 and plan.max_price == 800
    assert plan.must_haves == ["carbon frame", "56cm"]
    assert plan.nice_to_haves == ["Shimano 105"]
    assert plan.deal_breakers == ["crashed", "cracked"]
    assert plan.queries[:3] == ["cervelo", "trek emonda", "road bike"]


def test_nice_to_haves_and_synonym_queries():
    plan = parse_prompt("I'm looking for a sectional couch. Would be nice if it has a chaise")
    assert plan.queries == ["sectional couch", "sectional sofa", "sectional loveseat"]
    assert plan.nice_to_haves == ["a chaise"]


@pytest.mark.parametrize(
    "requirement, text, expected",
    [
        ("at least 60 inches wide", "Uplift desk, 72 x 30 inches", True),
        ("at least 60 inches wide", "Small desk, 48 inches", False),
        ("at least 60 inches wide", "Desk top is 6 ft long", True),
        ("at least 60 inches wide", "Nice desk", None),
        ("under 120k miles", "98K miles", True),
        ("under 120k miles", "Runs great, 150,000 miles", False),
        ("year 2016-2019", "2017 Toyota Tacoma", True),
        ("year 2016-2019", "2012 Toyota Tacoma", False),
        ("memory presets", "Has 4 memory presets", True),
        ("memory presets", "Height adjustable", None),
        ("grey", "Gray sectional", None),
    ],
)
def test_check_requirement(requirement, text, expected):
    assert check_requirement(requirement, ListingText(text), text) is expected


def test_deal_breakers_ignore_negated_mentions():
    assert not ListingText("No rust at all, rust-free frame").has_unnegated("rust")
    assert not ListingText("Rust free, garage kept").has_unnegated("rust")
    assert ListingText("Some surface rust on the bed").has_unnegated("rust")
    assert ListingText("Has a few stains").has_unnegated("stains")
    assert not ListingText("No pet hair, smoke free").has_unnegated("pet hair")


def test_evaluate_ranks_a_real_match_above_a_weak_one():
    plan = parse_prompt("Electric standing desk, at least 60 inches wide, no wobble, under $250, within 25 miles of Denver, CO")
    matcher = RulesMatcher()

    good = make_listing("1", "Electric standing desk 72 inch", 220, "Denver, CO")
    good.description = "Dual motor electric standing desk, 72 x 30 inches, no wobble at full height. Memory presets."
    good.distance_miles = 4
    weak = make_listing("2", "Office desk", 100, "Denver, CO")
    weak.description = "Wooden desk, 48 inches wide, a little wobble."
    weak.distance_miles = 8

    g = matcher.evaluate("", plan, good, [])
    w = matcher.evaluate("", plan, weak, [])
    assert g.match_score >= 90 and g.verdict == "perfect"
    assert "at least 60 inches wide" in g.matched_requirements
    assert w.match_score < 40 and w.verdict == "poor"
    assert "Mentions a deal-breaker: wobble" in w.red_flags
    assert "Not met: at least 60 inches wide" in w.missing_or_unclear

    good.evaluation, weak.evaluation = g, w
    final = matcher.final_pick("", plan, [weak, good])
    assert final.best_id == "1" and final.ranking == ["1", "2"]


def test_evaluate_flags_scam_signals():
    plan = parse_prompt("PS5 under $400 near Austin, TX")
    listing = make_listing("1", "PS5 console", 150, "Austin, TX")
    listing.description = "PS5 brand new in box. Pay with Zelle first, text me at 512-555-0199"
    flags = RulesMatcher().evaluate("", plan, listing, []).red_flags
    assert any("zelle" in f for f in flags)
    assert "Asks you to contact them outside Facebook" in flags


def test_final_pick_flags_suspiciously_cheap_listings_and_needs_a_decent_score():
    plan = parse_prompt("PS5 near Austin, TX")
    matcher = RulesMatcher()
    listings = []
    for i, price in enumerate([400, 380, 420, 90], 1):
        l = make_listing(str(i), "PS5 console with controller", price, "Austin, TX")
        l.description = "Works perfectly, comes with one controller and all cables."
        l.evaluation = matcher.evaluate("", plan, l, [])
        listings.append(l)
    matcher.final_pick("", plan, listings)
    assert any("far below similar listings" in f for f in listings[3].evaluation.red_flags)
    assert not any("far below" in f for f in listings[0].evaluation.red_flags)

    poor = make_listing("9", "Desk lamp", 10, "Austin, TX")
    poor.evaluation = matcher.evaluate("", plan, poor, [])
    assert matcher.final_pick("", plan, [poor]).best_id == ""


def test_triage_prefers_titles_that_name_the_item():
    plan = parse_prompt("Electric standing desk under $250")
    scores = RulesMatcher().triage(
        "", plan, [make_listing("1", "Electric standing desk", 200, ""), make_listing("2", "Desk lamp", 20, "")]
    )
    assert scores["1"][0] > scores["2"][0]


def test_word_forms_match():
    text = ListingText("Sit stand desk frame, adjusts smoothly")
    assert text.has("standing") and text.has("adjust") and text.has("frames")
