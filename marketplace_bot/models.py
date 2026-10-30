"""Data models shared across the bot.

The ``SearchPlan``, ``TriageBatch``, ``ListingEvaluation`` and ``FinalRanking``
models double as Claude structured-output schemas, so their field descriptions
are written as instructions to the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchPlan(BaseModel):
    """What the user wants, turned into concrete Marketplace searches."""

    summary: str = Field(description="One sentence restating exactly what the user is looking for.")
    queries: list[str] = Field(
        description=(
            "3-8 short Marketplace search queries that together cover every way a seller might title "
            "this item: the exact item, brand/model variants, common synonyms, and common misspellings. "
            "Keep each query 1-5 words; Marketplace search works best with short queries."
        )
    )
    location: str = Field(
        description=(
            "The place the user wants to search around (city + state/region, or a ZIP/postal code), "
            "exactly as it should be geocoded. Empty string if the user did not say."
        )
    )
    radius_miles: float | None = Field(
        description="Maximum distance from the location, in miles (convert km to miles). Null if not stated."
    )
    min_price: int | None = Field(description="Minimum price in whole currency units, or null.")
    max_price: int | None = Field(description="Maximum price in whole currency units, or null.")
    days_since_listed: int | None = Field(
        description="Only include listings posted within this many days, or null for any age."
    )
    must_haves: list[str] = Field(
        description="Hard requirements a listing must meet to be a match. Each one specific and checkable."
    )
    nice_to_haves: list[str] = Field(description="Preferences that make a listing better but are not required.")
    deal_breakers: list[str] = Field(
        description="Things that disqualify a listing (e.g. damage, salvage title, 'parts only')."
    )
    exclude_title_keywords: list[str] = Field(
        description=(
            "Lowercase words or phrases that, if they appear in a listing title, mean it is certainly "
            "not a match (e.g. 'wanted', 'iso', 'parts only', 'case only'). Keep this conservative."
        )
    )
    local_pickup_only: bool = Field(
        description="True unless the user says shipped items are acceptable."
    )

    def budget_text(self) -> str:
        if self.min_price is not None and self.max_price is not None:
            return f"{self.min_price:,} - {self.max_price:,}"
        if self.max_price is not None:
            return f"up to {self.max_price:,}"
        if self.min_price is not None:
            return f"{self.min_price:,} or more"
        return "any"


class TriageItem(BaseModel):
    id: str = Field(description="The listing id, copied exactly.")
    score: int = Field(description="0-10: how likely this listing is to be what the user wants.")
    reason: str = Field(description="Under 15 words.")


class TriageBatch(BaseModel):
    items: list[TriageItem] = Field(description="One entry for every listing provided, in any order.")


class ListingEvaluation(BaseModel):
    match_score: int = Field(description="0-100: how well this listing matches the user's request.")
    verdict: Literal["perfect", "strong", "possible", "poor"]
    summary: str = Field(description="2-3 sentences on why this listing does or does not fit.")
    matched_requirements: list[str] = Field(description="Requirements this listing clearly satisfies.")
    missing_or_unclear: list[str] = Field(description="Requirements not met, or that the listing does not confirm.")
    red_flags: list[str] = Field(
        description="Scam signals, damage, suspicious pricing, vague details, or other concerns. Empty if none."
    )
    questions_for_seller: list[str] = Field(description="The most useful questions to ask before buying.")
    price_assessment: str = Field(description="One sentence on whether the price looks fair for what it is.")


class FinalRanking(BaseModel):
    best_id: str = Field(description="The id of the single best listing, or an empty string if none fit.")
    ranking: list[str] = Field(description="All provided listing ids, best first.")
    explanation: str = Field(description="2-4 sentences on why the best listing wins over the runners-up.")


class Listing(BaseModel):
    """A Marketplace listing as the bot sees it, from search card through evaluation."""

    id: str
    url: str
    title: str = ""
    price: float | None = None
    price_text: str = ""
    location: str = ""
    extras: list[str] = Field(default_factory=list)
    image_url: str = ""
    found_by: list[str] = Field(default_factory=list)

    distance_miles: float | None = None

    # Filled in from the listing page.
    description: str = ""
    page_text: str = ""
    listed_ago: str = ""
    image_urls: list[str] = Field(default_factory=list)

    triage_score: int | None = None
    triage_reason: str = ""
    evaluation: ListingEvaluation | None = None
    skip_reason: str = ""
    error: str = ""
