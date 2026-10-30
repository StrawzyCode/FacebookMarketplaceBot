from __future__ import annotations

import pytest

from marketplace_bot.config import Settings
from marketplace_bot.geo import GeoLocator
from marketplace_bot.models import (
    FinalRanking,
    Listing,
    ListingEvaluation,
    SearchPlan,
)
from marketplace_bot.parsing import item_url

PLACES = {
    "austin, tx": (30.2672, -97.7431, "us"),
    "round rock, tx": (30.5083, -97.6789, "us"),  # ~17 mi from Austin
    "san antonio, tx": (29.4241, -98.4936, "us"),  # ~73 mi from Austin
}


def fake_geocode(query: str, country_code: str | None):
    return PLACES.get(query.lower())


@pytest.fixture
def geo(tmp_path):
    return GeoLocator(tmp_path / "geo.json", fake_geocode)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        default_location="Austin, TX",
        default_radius_miles=25,
        profile_dir=tmp_path / "profile",
        cache_dir=tmp_path / "cache",
        results_dir=tmp_path / "results",
        max_deep_checks=10,
        ai_workers=2,
        min_delay_seconds=0,
        max_delay_seconds=0,
    )


@pytest.fixture
def plan():
    return SearchPlan(
        summary="A used electric standing desk under $300",
        queries=["standing desk", "electric desk", "Standing Desk "],
        location="Austin, TX",
        radius_miles=30,
        min_price=None,
        max_price=300,
        days_since_listed=None,
        must_haves=["electric height adjustment"],
        nice_to_haves=["memory presets"],
        deal_breakers=["broken motor"],
        exclude_title_keywords=["wanted"],
        local_pickup_only=True,
    )


def make_listing(item_id: str, title: str, price: float | None, location: str) -> Listing:
    return Listing(
        id=item_id,
        url=item_url(item_id),
        title=title,
        price=price,
        price_text="" if price is None else f"${price:,.0f}",
        location=location,
    )


class FakeScraper:
    """Stands in for the browser: returns canned search results and listing pages."""

    def __init__(self, results: dict[str, list[Listing]], details: dict[str, dict] | None = None):
        self.results = results
        self.details = details or {}
        self.searched: list[str] = []
        self.opened: list[str] = []

    def ensure_logged_in(self):
        pass

    def search(self, query, plan):
        self.searched.append(query)
        out = []
        for l in self.results.get(query, []):
            copy = l.model_copy(deep=True)
            copy.found_by = [query]
            out.append(copy)
        return out

    def fetch_details(self, listing):
        self.opened.append(listing.id)
        for key, value in self.details.get(listing.id, {}).items():
            setattr(listing, key, value)

    def download_images(self, urls):
        return [("image/jpeg", b"jpeg-bytes")] if urls else []


class FakeAI:
    """Stands in for Claude: scores listings from a lookup table."""

    def __init__(self, scores: dict[str, int], triage: dict[str, int] | None = None, best: str | None = None):
        self.scores = scores
        self.triage_scores = triage or {}
        self.best = best
        self.triaged: list[str] = []
        self.evaluated: list[str] = []
        self.finalists: list[str] = []

    def triage(self, prompt, plan, listings):
        self.triaged.extend(l.id for l in listings)
        return {l.id: (self.triage_scores.get(l.id, 5), "fake") for l in listings if l.id in self.triage_scores}

    def evaluate(self, prompt, plan, listing, images):
        self.evaluated.append(listing.id)
        score = self.scores.get(listing.id, 50)
        verdict = "perfect" if score >= 90 else "strong" if score >= 70 else "possible" if score >= 40 else "poor"
        return ListingEvaluation(
            match_score=score,
            verdict=verdict,
            summary=f"Listing {listing.id} scored {score}.",
            matched_requirements=[],
            missing_or_unclear=[],
            red_flags=[],
            questions_for_seller=[],
            price_assessment="Fair.",
        )

    def final_pick(self, prompt, plan, listings):
        self.finalists = [l.id for l in listings]
        ids = [l.id for l in listings]
        best = self.best if self.best in ids else ids[0]
        return FinalRanking(best_id=best, ranking=[best] + [i for i in ids if i != best], explanation="Because.")
