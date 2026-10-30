"""The search funnel: plan -> search every query -> filter by distance/budget -> triage -> deep-check -> pick."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Protocol

import anthropic
from playwright.sync_api import Error as PlaywrightError

from .ai import TRIAGE_BATCH_SIZE, AIError
from .config import Settings
from .geo import GeoLocator, miles_between
from .models import FinalRanking, Listing, ListingEvaluation, SearchPlan
from .parsing import title_has_excluded_keyword
from .scraper import NotLoggedInError

log = logging.getLogger(__name__)

MIN_TRIAGE_SCORE = 3
FINALISTS = 8
FINALIST_MIN_SCORE = 40

# Errors that mean the setup is wrong (bad key, bad model name...), not that one listing failed.
FATAL_API_ERRORS = (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError)


class Matcher(Protocol):
    """What the pipeline needs from a matching backend (rules, local AI or Claude)."""

    uses_images: bool

    def plan(self, prompt: str, default_location: str, default_radius_miles: float | None) -> SearchPlan: ...

    def triage(self, prompt: str, plan: SearchPlan, listings: list[Listing]) -> dict[str, tuple[int, str]]: ...

    def evaluate(self, prompt: str, plan: SearchPlan, listing: Listing, images) -> ListingEvaluation: ...

    def final_pick(self, prompt: str, plan: SearchPlan, listings: list[Listing]) -> FinalRanking: ...


@dataclass
class Overrides:
    location: str | None = None
    radius_miles: float | None = None
    min_price: int | None = None
    max_price: int | None = None


@dataclass
class RunResult:
    prompt: str
    plan: SearchPlan
    listings: list[Listing]
    ranked: list[Listing]
    best: Listing | None
    explanation: str
    stats: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def plan_search(ai: Matcher, prompt: str, settings: Settings, overrides: Overrides | None = None) -> SearchPlan:
    plan = ai.plan(prompt, settings.default_location, settings.default_radius_miles)
    o = overrides or Overrides()
    if o.location:
        plan.location = o.location
    if o.radius_miles is not None:
        plan.radius_miles = o.radius_miles
    if o.min_price is not None:
        plan.min_price = o.min_price
    if o.max_price is not None:
        plan.max_price = o.max_price
    if not plan.location:
        plan.location = settings.default_location
    if plan.radius_miles is None:
        plan.radius_miles = settings.default_radius_miles

    seen: set[str] = set()
    queries = []
    for q in plan.queries:
        q = " ".join(q.split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            queries.append(q)
    plan.queries = queries
    return plan


def run_search(
    prompt: str,
    plan: SearchPlan,
    settings: Settings,
    scraper,
    ai: Matcher,
    geo: GeoLocator,
    deep_limit: int | None = None,
    say: Callable[[str], None] = print,
) -> RunResult:
    deep_limit = (settings.max_deep_checks or 40) if deep_limit is None else deep_limit
    workers = max(1, settings.ai_workers or 1)
    warnings: list[str] = []

    # 1. Where is the shopper?
    home = geo.lookup(plan.location) if plan.location else None
    if plan.location and home is None:
        raise ValueError(f"Couldn't find the location {plan.location!r}. Try a city + state or a ZIP code.")
    if home is None:
        warnings.append("No location given, so results were not filtered by distance.")
    elif not plan.radius_miles:
        warnings.append("No distance given, so results were not filtered by distance.")
    country = (home[2] or None) if home else None

    # 2. Run every search query and merge the results.
    scraper.ensure_logged_in()
    found: dict[str, Listing] = {}
    for i, query in enumerate(plan.queries, 1):
        try:
            results = scraper.search(query, plan)
        except NotLoggedInError:
            raise
        except PlaywrightError as exc:
            warnings.append(f"Search {query!r} failed: {exc}")
            say(f"  [{i}/{len(plan.queries)}] {query!r}: failed ({exc})")
            continue
        new = 0
        for listing in results:
            if listing.id in found:
                found[listing.id].found_by.extend(q for q in listing.found_by if q not in found[listing.id].found_by)
            else:
                found[listing.id] = listing
                new += 1
        say(f"  [{i}/{len(plan.queries)}] {query!r}: {len(results)} listings, {new} new")
    listings = list(found.values())
    say(f"Found {len(listings)} unique listings.")

    # 3. Cheap filters: title keywords, budget, distance.
    places = {l.location for l in listings if l.location}
    if home and plan.radius_miles and places:
        say(f"Checking distance for {len(places)} places (first run is slower; results are cached)...")
    for listing in listings:
        _prefilter(listing, plan, home, country, geo)
    candidates = [l for l in listings if not l.skip_reason]
    too_far = sum(1 for l in listings if l.skip_reason.startswith("too far"))
    if listings and too_far / len(listings) > 0.7:
        warnings.append(
            f"{too_far} of {len(listings)} listings were outside your radius. Facebook may be searching "
            "around a different area: set your Marketplace location on facebook.com (or FB_LOCATION_ID in .env) "
            f"to {plan.location}."
        )
    say(f"{len(candidates)} listings pass the distance/budget filters.")

    # 4. Triage on card info so only the promising ones get opened.
    if len(candidates) > deep_limit:
        say(f"Screening {len(candidates)} listings to pick the {deep_limit} most promising...")
        _triage(ai, prompt, plan, candidates, workers)
        candidates.sort(key=lambda l: (-(l.triage_score or 0), l.distance_miles or 0))
        shortlist = []
        for l in candidates:
            if (l.triage_score or 0) < MIN_TRIAGE_SCORE:
                l.skip_reason = f"screened out: {l.triage_reason}"
            elif len(shortlist) >= deep_limit:
                l.skip_reason = "not in the top listings to inspect (raise --max-deep or use --deep-all)"
            else:
                shortlist.append(l)
    else:
        shortlist = candidates

    # 5. Open each shortlisted listing and have Claude inspect it (pages load while earlier ones are scored).
    say(f"Inspecting {len(shortlist)} listings in depth...")
    evaluated: list[Listing] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, listing in enumerate(shortlist, 1):
            say(f"  [{i}/{len(shortlist)}] {listing.title[:70]}")
            images: list[tuple[str, bytes]] = []
            try:
                scraper.fetch_details(listing)
                if getattr(ai, "uses_images", True):
                    images = scraper.download_images(listing.image_urls)
            except NotLoggedInError:
                raise
            except PlaywrightError as exc:
                listing.error = f"Couldn't fully load the listing page: {exc}"
            if listing.distance_miles is None:
                _apply_distance(listing, plan, home, country, geo)
                if listing.skip_reason:
                    continue
            futures[pool.submit(_evaluate, ai, prompt, plan, listing, images)] = listing
        for future in as_completed(futures):
            listing = futures[future]
            future.result()
            if listing.evaluation:
                evaluated.append(listing)

    # 6. Rank, then have Claude make the final call between the top contenders.
    evaluated.sort(key=lambda l: (-l.evaluation.match_score, l.distance_miles if l.distance_miles is not None else 1e9))
    finalists = [l for l in evaluated[:FINALISTS] if l.evaluation.match_score >= FINALIST_MIN_SCORE]
    best, explanation = None, ""
    if finalists:
        say("Comparing the finalists...")
        try:
            final = ai.final_pick(prompt, plan, finalists)
            by_id = {l.id: l for l in finalists}
            order = list(dict.fromkeys(final.ranking + [l.id for l in finalists]))
            evaluated = [by_id[i] for i in order] + evaluated[len(finalists):]
            best, explanation = by_id.get(final.best_id), final.explanation
        except FATAL_API_ERRORS:
            raise
        except (AIError, anthropic.APIError) as exc:
            warnings.append(f"Final comparison failed ({exc}); ranked by score instead.")
            best = finalists[0]
            explanation = best.evaluation.summary

    stats = {
        "queries": len(plan.queries),
        "found": len(listings),
        "too_far": sum(1 for l in listings if l.skip_reason.startswith("too far")),
        "over_budget": sum(1 for l in listings if l.skip_reason.startswith("outside budget")),
        "excluded_by_title": sum(1 for l in listings if l.skip_reason.startswith("title mentions")),
        "inspected": len(evaluated),
        "strong_or_better": sum(1 for l in evaluated if l.evaluation.match_score >= 70),
    }
    return RunResult(prompt, plan, listings, evaluated, best, explanation, stats, warnings)


def _prefilter(listing: Listing, plan: SearchPlan, home, country: str | None, geo: GeoLocator) -> None:
    keyword = title_has_excluded_keyword(listing.title, plan.exclude_title_keywords)
    if keyword:
        listing.skip_reason = f"title mentions {keyword!r}"
        return
    if listing.price is not None:
        if plan.max_price is not None and listing.price > plan.max_price:
            listing.skip_reason = f"outside budget ({listing.price_text})"
            return
        if plan.min_price is not None and listing.price < plan.min_price:
            listing.skip_reason = f"outside budget ({listing.price_text})"
            return
    _apply_distance(listing, plan, home, country, geo)


def _apply_distance(listing: Listing, plan: SearchPlan, home, country: str | None, geo: GeoLocator) -> None:
    if not home or not listing.location:
        return
    point = geo.lookup(listing.location, country)
    if point is None:
        return
    listing.distance_miles = miles_between(home[:2], point[:2])
    if plan.radius_miles and listing.distance_miles > plan.radius_miles:
        listing.skip_reason = f"too far ({listing.distance_miles:.0f} mi away)"


def _triage(ai: Matcher, prompt: str, plan: SearchPlan, candidates: list[Listing], workers: int) -> None:
    batches = [candidates[i : i + TRIAGE_BATCH_SIZE] for i in range(0, len(candidates), TRIAGE_BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(ai.triage, prompt, plan, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                scores = future.result()
            except FATAL_API_ERRORS:
                raise
            except (AIError, anthropic.APIError) as exc:
                log.warning("Screening batch failed (%s); keeping its listings in the running.", exc)
                scores = {}
            for listing in batch:
                # Anything Claude didn't score stays in the middle of the pack rather than being dropped.
                listing.triage_score, listing.triage_reason = scores.get(listing.id, (5, "not screened"))


def _evaluate(ai: Matcher, prompt: str, plan: SearchPlan, listing: Listing, images) -> None:
    try:
        try:
            listing.evaluation = ai.evaluate(prompt, plan, listing, images)
        except anthropic.BadRequestError:
            if not images:
                raise
            # A photo the API can't read shouldn't sink the whole listing; retry on text alone.
            listing.evaluation = ai.evaluate(prompt, plan, listing, [])
    except FATAL_API_ERRORS:
        raise
    except (AIError, anthropic.APIError) as exc:
        listing.error = f"Couldn't evaluate: {exc}"
