import csv
import json
from datetime import datetime
from pathlib import Path

from marketplace_bot.models import ListingEvaluation
from marketplace_bot.pipeline import RunResult
from marketplace_bot.report import results_folder, write_results

from .conftest import make_listing


def test_writes_html_csv_and_json_with_escaping(tmp_path, plan):
    best = make_listing("1", "Desk <script>alert(1)</script>", 200, "Austin, TX")
    best.distance_miles = 3.2
    best.evaluation = ListingEvaluation(
        match_score=93, verdict="perfect", summary="Exactly what you asked for.",
        matched_requirements=["electric"], missing_or_unclear=[], red_flags=[],
        questions_for_seller=["Any wobble?"], price_assessment="Good price.",
    )
    skipped = make_listing("2", "Far desk", 100, "San Antonio, TX")
    skipped.skip_reason = "too far (73 mi away)"
    result = RunResult(
        prompt="desk", plan=plan, listings=[best, skipped], ranked=[best], best=best,
        explanation="Closest and cheapest.", stats={"found": 2, "inspected": 1}, warnings=["Heads up & beware"],
    )

    paths = write_results(result, tmp_path / "out")

    html = paths["html"].read_text()
    assert "<script>alert(1)</script>" not in html
    assert "Desk &lt;script&gt;" in html
    assert "Best match" in html and "Closest and cheapest." in html
    assert "too far (73 mi away)" in html
    assert "Heads up &amp; beware" in html

    rows = list(csv.DictReader(paths["csv"].open()))
    assert rows[0]["score"] == "93" and rows[0]["url"] == best.url and rows[0]["distance_miles"] == "3.2"

    data = json.loads(paths["json"].read_text())
    assert data["best_id"] == "1"
    assert len(data["all_listings"]) == 2


def test_results_folder_name():
    folder = results_folder(Path("/r"), "A used Electric Desk!", datetime(2026, 1, 2, 3, 4, 5))
    assert folder == Path("/r/2026-01-02_030405_a-used-electric-desk")


def test_budget_text(plan):
    assert plan.budget_text() == "up to 300"
    assert plan.model_copy(update={"min_price": 1000, "max_price": 2500}).budget_text() == "1,000 - 2,500"
    assert plan.model_copy(update={"min_price": 50, "max_price": None}).budget_text() == "50 or more"
    assert plan.model_copy(update={"max_price": None}).budget_text() == "any"
