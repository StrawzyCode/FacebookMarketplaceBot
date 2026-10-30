import anthropic
import httpx2
import pytest

from marketplace_bot.ai import AIError
from marketplace_bot.pipeline import Overrides, plan_search, run_search

from .conftest import FakeAI, FakeScraper, make_listing


def quiet(_msg):
    pass


def test_plan_search_dedupes_queries_and_applies_overrides(settings, plan):
    class PlanAI:
        def plan(self, prompt, default_location, default_radius):
            return plan.model_copy(deep=True)

    result = plan_search(PlanAI(), "desk", settings, Overrides(radius_miles=10, max_price=150))
    assert result.queries == ["standing desk", "electric desk"]
    assert result.radius_miles == 10
    assert result.max_price == 150

    no_location = plan.model_copy(update={"location": "", "radius_miles": None})

    class NoLocationAI:
        def plan(self, *args):
            return no_location.model_copy(deep=True)

    result = plan_search(NoLocationAI(), "desk", settings)
    assert result.location == "Austin, TX"
    assert result.radius_miles == 25


def test_filters_by_distance_budget_and_title_then_ranks(settings, plan, geo):
    plan.queries = ["standing desk", "electric desk"]
    near = make_listing("1", "Electric standing desk", 200, "Round Rock, TX")
    far = make_listing("2", "Standing desk", 150, "San Antonio, TX")
    pricey = make_listing("3", "Uplift desk", 450, "Austin, TX")
    wanted = make_listing("4", "WANTED standing desk", 50, "Austin, TX")
    dup = make_listing("5", "Jarvis electric desk", 250, "Austin, TX")
    hidden_far = make_listing("6", "Motorized desk", 100, "")
    scraper = FakeScraper(
        {
            "standing desk": [near, far, pricey, wanted, dup],
            "electric desk": [dup, hidden_far],
        },
        details={"6": {"location": "San Antonio, TX"}, "1": {"image_urls": ["https://scontent.fbcdn.net/a.jpg"]}},
    )
    ai = FakeAI(scores={"1": 72, "5": 95}, best="5")

    result = run_search("desk", plan, settings, scraper, ai, geo, say=quiet)

    by_id = {l.id: l for l in result.listings}
    assert set(by_id) == {"1", "2", "3", "4", "5", "6"}
    assert by_id["5"].found_by == ["standing desk", "electric desk"]
    assert by_id["2"].skip_reason.startswith("too far")
    assert by_id["3"].skip_reason.startswith("outside budget")
    assert by_id["4"].skip_reason == "title mentions 'wanted'"
    # Its location only showed up on the listing page, and it's out of range, so it isn't scored.
    assert by_id["6"].skip_reason.startswith("too far")
    assert sorted(ai.evaluated) == ["1", "5"]
    assert round(by_id["1"].distance_miles) == 17

    assert [l.id for l in result.ranked] == ["5", "1"]
    assert result.best.id == "5"
    assert result.stats["found"] == 6
    assert result.stats["too_far"] == 2
    assert result.stats["inspected"] == 2
    assert result.stats["strong_or_better"] == 2


def test_triage_limits_deep_checks_to_most_promising(settings, plan, geo):
    listings = [make_listing(str(i), f"Desk {i}", 100, "Austin, TX") for i in range(1, 6)]
    scraper = FakeScraper({"standing desk": listings})
    plan.queries = ["standing desk"]
    ai = FakeAI(scores={}, triage={"1": 2, "2": 9, "3": 7, "4": 8, "5": 4})

    result = run_search("desk", plan, settings, scraper, ai, geo, deep_limit=2, say=quiet)

    assert sorted(ai.triaged) == ["1", "2", "3", "4", "5"]
    assert scraper.opened == ["2", "4"]
    by_id = {l.id: l for l in result.listings}
    assert by_id["1"].skip_reason.startswith("screened out")
    assert by_id["3"].skip_reason.startswith("not in the top")


def test_no_finalist_when_everything_scores_poorly(settings, plan, geo):
    plan.queries = ["standing desk"]
    scraper = FakeScraper({"standing desk": [make_listing("1", "Desk lamp", 20, "Austin, TX")]})
    ai = FakeAI(scores={"1": 10})

    result = run_search("desk", plan, settings, scraper, ai, geo, say=quiet)

    assert result.best is None
    assert [l.id for l in result.ranked] == ["1"]
    assert ai.finalists == []


def test_unknown_location_is_an_error(settings, plan, geo):
    plan.location = "Nowhereville"
    with pytest.raises(ValueError, match="Nowhereville"):
        run_search("desk", plan, settings, FakeScraper({}), FakeAI({}), geo, say=quiet)


def test_one_failed_evaluation_does_not_stop_the_run(settings, plan, geo):
    class FlakyAI(FakeAI):
        def evaluate(self, prompt, plan, listing, images):
            if listing.id == "1":
                raise AIError("cut off")
            return super().evaluate(prompt, plan, listing, images)

    plan.queries = ["standing desk"]
    scraper = FakeScraper(
        {"standing desk": [make_listing("1", "Desk A", 100, "Austin, TX"), make_listing("2", "Desk B", 100, "Austin, TX")]}
    )
    result = run_search("desk", plan, settings, scraper, FlakyAI({"2": 80}), geo, say=quiet)

    assert [l.id for l in result.ranked] == ["2"]
    assert "cut off" in {l.id: l for l in result.listings}["1"].error


def test_bad_api_key_stops_the_run(settings, plan, geo):
    class BadKeyAI(FakeAI):
        def evaluate(self, *args):
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=request), body=None)

    plan.queries = ["standing desk"]
    scraper = FakeScraper({"standing desk": [make_listing("1", "Desk", 100, "Austin, TX")]})
    with pytest.raises(anthropic.AuthenticationError):
        run_search("desk", plan, settings, scraper, BadKeyAI({}), geo, say=quiet)


def test_duplicate_ids_in_final_ranking_are_ignored(settings, plan, geo):
    class RepeatingAI(FakeAI):
        def final_pick(self, prompt, plan, listings):
            result = super().final_pick(prompt, plan, listings)
            result.ranking = result.ranking + result.ranking
            return result

    plan.queries = ["standing desk"]
    scraper = FakeScraper(
        {"standing desk": [make_listing("1", "Desk A", 100, "Austin, TX"), make_listing("2", "Desk B", 100, "Austin, TX")]}
    )
    result = run_search("desk", plan, settings, scraper, RepeatingAI({"1": 80, "2": 85}, best="1"), geo, say=quiet)

    assert [l.id for l in result.ranked] == ["1", "2"]
