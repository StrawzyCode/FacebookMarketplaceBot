"""Writes the results: an HTML report to read, plus CSV and JSON for spreadsheets or other tools."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path

from .models import Listing
from .pipeline import RunResult

VERDICT_LABELS = {"perfect": "Perfect match", "strong": "Strong match", "possible": "Possible", "poor": "Poor fit"}


def results_folder(base: Path, plan_summary: str, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    slug = re.sub(r"[^a-z0-9]+", "-", plan_summary.lower()).strip("-")[:40] or "search"
    return base / f"{now:%Y-%m-%d_%H%M%S}_{slug}"


def write_results(result: RunResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "html": out_dir / "report.html",
        "csv": out_dir / "matches.csv",
        "json": out_dir / "results.json",
    }
    paths["html"].write_text(render_html(result), encoding="utf-8")
    _write_csv(result, paths["csv"])
    _write_json(result, paths["json"])
    return paths


def _price(l: Listing) -> str:
    if l.price_text:
        return l.price_text
    return "Price not shown" if l.price is None else f"{l.price:g}"


def _distance(l: Listing) -> str:
    return "distance unknown" if l.distance_miles is None else f"{l.distance_miles:.0f} mi away"


def _write_csv(result: RunResult, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "score", "verdict", "title", "price", "distance_miles", "location", "listed",
                    "url", "summary", "red_flags", "questions_for_seller"])
        for rank, l in enumerate(result.ranked, 1):
            ev = l.evaluation
            w.writerow([
                rank, ev.match_score, ev.verdict, l.title, _price(l),
                "" if l.distance_miles is None else f"{l.distance_miles:.1f}",
                l.location, l.listed_ago, l.url, ev.summary,
                " | ".join(ev.red_flags), " | ".join(ev.questions_for_seller),
            ])


def _write_json(result: RunResult, path: Path) -> None:
    data = {
        "prompt": result.prompt,
        "plan": result.plan.model_dump(),
        "best_id": result.best.id if result.best else None,
        "explanation": result.explanation,
        "stats": result.stats,
        "warnings": result.warnings,
        "ranked": [l.model_dump() for l in result.ranked],
        "all_listings": [l.model_dump(exclude={"page_text"}) for l in result.listings],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _list(title: str, items: list[str], cls: str = "") -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{escape(i)}</li>" for i in items)
    return f'<div class="block {cls}"><h4>{escape(title)}</h4><ul>{lis}</ul></div>'


def _card(l: Listing, rank: int, best: bool) -> str:
    ev = l.evaluation
    img = (
        f'<img src="{escape(l.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        if l.image_url else '<div class="noimg">No photo</div>'
    )
    tag = '<span class="best-tag">Best match</span>' if best else f'<span class="rank">#{rank}</span>'
    meta = " · ".join(escape(x) for x in [_price(l), _distance(l), l.location, f"listed {l.listed_ago}" if l.listed_ago else ""] if x)
    return f"""
<article class="card{' best' if best else ''}">
  <a class="photo" href="{escape(l.url)}" target="_blank" rel="noopener">{img}</a>
  <div class="body">
    <div class="head">{tag}<span class="score v-{escape(ev.verdict)}">{ev.match_score} · {escape(VERDICT_LABELS.get(ev.verdict, ev.verdict))}</span></div>
    <h3><a href="{escape(l.url)}" target="_blank" rel="noopener">{escape(l.title or 'Untitled listing')}</a></h3>
    <p class="meta">{meta}</p>
    <p>{escape(ev.summary)}</p>
    <div class="grid">
      {_list("Matches", ev.matched_requirements, "good")}
      {_list("Missing or unclear", ev.missing_or_unclear, "warn")}
      {_list("Red flags", ev.red_flags, "bad")}
      {_list("Ask the seller", ev.questions_for_seller)}
    </div>
    <p class="price-note">{escape(ev.price_assessment)}</p>
  </div>
</article>"""


def render_html(result: RunResult) -> str:
    plan = result.plan
    best_id = result.best.id if result.best else None
    cards = "".join(_card(l, i, l.id == best_id) for i, l in enumerate(result.ranked, 1))
    if not cards:
        cards = '<p class="empty">No listings could be inspected. Check the warnings above and try a broader search.</p>'

    facts = [
        ("Looking for", plan.summary),
        ("Around", f"{plan.location or 'your Facebook location'}" + (f" (within {plan.radius_miles:g} mi)" if plan.radius_miles else "")),
        ("Budget", plan.budget_text()),
        ("Searches run", ", ".join(plan.queries)),
        ("Must have", "; ".join(plan.must_haves) or "—"),
        ("Deal-breakers", "; ".join(plan.deal_breakers) or "—"),
    ]
    facts_html = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in facts)
    s = result.stats
    stats_html = (
        f"{s.get('found', 0)} listings found · {s.get('too_far', 0)} too far · {s.get('over_budget', 0)} over budget · "
        f"{s.get('inspected', 0)} inspected in depth · {s.get('strong_or_better', 0)} strong or perfect"
    )
    warnings = "".join(f"<li>{escape(w)}</li>" for w in result.warnings)
    warnings_html = f'<ul class="warnings">{warnings}</ul>' if warnings else ""
    verdict = ""
    if result.best:
        verdict = f'<section class="verdict"><h2>Our pick</h2><p>{escape(result.explanation)}</p></section>'
    elif result.ranked:
        why = result.explanation or "None of the listings fully fit your request."
        verdict = f'<section class="verdict"><h2>No great match yet</h2><p>{escape(why)} The closest ones are below.</p></section>'

    skipped_rows = "".join(
        f'<tr><td><a href="{escape(l.url)}" target="_blank" rel="noopener">{escape(l.title or l.id)}</a></td>'
        f"<td>{escape(_price(l))}</td><td>{escape(_distance(l))}</td>"
        f"<td>{escape(l.skip_reason or l.error or ('screened: ' + l.triage_reason if l.triage_reason else ''))}</td></tr>"
        for l in result.listings
        if l.evaluation is None
    )
    skipped_html = (
        f'<details><summary>Other listings found but not inspected ({sum(1 for l in result.listings if l.evaluation is None)})</summary>'
        f"<table><thead><tr><th>Listing</th><th>Price</th><th>Distance</th><th>Why skipped</th></tr></thead>"
        f"<tbody>{skipped_rows}</tbody></table></details>"
        if skipped_rows else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marketplace Matches</title>
<style>
:root {{
  --bg: #f6f7f9; --panel: #ffffff; --text: #1c1e21; --muted: #65676b; --line: #dadde1;
  --accent: #1b74e4; --good: #1e7e34; --warn: #a15c00; --bad: #c62828; --best: #fff7e0; --best-line: #e0a800;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #18191a; --panel: #242526; --text: #e4e6eb; --muted: #b0b3b8; --line: #3a3b3c;
    --accent: #4599ff; --good: #5cc275; --warn: #f0a640; --bad: #ff6b6b; --best: #2e2a1c; --best-line: #c99a14;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
a {{ color: var(--accent); }}
.sub {{ color: var(--muted); margin: 0 0 20px; }}
dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 0 0 16px; }}
dt {{ color: var(--muted); }} dd {{ margin: 0; }}
.warnings {{ background: var(--panel); border-left: 4px solid var(--warn); padding: 12px 12px 12px 32px; border-radius: 6px; }}
.verdict {{ background: var(--best); border: 1px solid var(--best-line); border-radius: 10px; padding: 12px 16px; margin: 16px 0; }}
.verdict h2 {{ margin: 0 0 4px; font-size: 1.1rem; }}
.verdict p {{ margin: 0; }}
.card {{ display: grid; grid-template-columns: 220px 1fr; gap: 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin: 14px 0; }}
.card.best {{ border: 2px solid var(--best-line); }}
.photo img, .noimg {{ width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 8px; background: var(--line); display: flex; align-items: center; justify-content: center; color: var(--muted); }}
.head {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.rank {{ color: var(--muted); font-weight: 600; }}
.best-tag {{ background: var(--best-line); color: #000; font-weight: 700; padding: 2px 8px; border-radius: 999px; font-size: .8rem; }}
.score {{ font-weight: 700; font-size: .85rem; padding: 2px 8px; border-radius: 999px; border: 1px solid currentColor; }}
.v-perfect, .v-strong {{ color: var(--good); }} .v-possible {{ color: var(--warn); }} .v-poor {{ color: var(--bad); }}
.card h3 {{ margin: 6px 0 2px; font-size: 1.1rem; }}
.card h3 a {{ color: var(--text); text-decoration: none; }}
.card h3 a:hover {{ text-decoration: underline; }}
.meta {{ color: var(--muted); margin: 0 0 8px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; }}
.block h4 {{ margin: 6px 0 2px; font-size: .85rem; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }}
.block ul {{ margin: 0; padding-left: 18px; }}
.good h4 {{ color: var(--good); }} .warn h4 {{ color: var(--warn); }} .bad h4 {{ color: var(--bad); }}
.price-note {{ color: var(--muted); font-style: italic; margin: 8px 0 0; }}
details {{ margin-top: 24px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; }}
summary {{ cursor: pointer; font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: .9rem; }}
th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
.empty {{ color: var(--muted); }}
@media (max-width: 640px) {{
  .card {{ grid-template-columns: 1fr; }}
  .photo img, .noimg {{ aspect-ratio: 4 / 3; }}
  .grid {{ grid-template-columns: 1fr; }}
  dl {{ grid-template-columns: 1fr; }}
  table {{ display: block; overflow-x: auto; }}
}}
</style>
</head>
<body>
<main>
  <h1>Marketplace matches</h1>
  <p class="sub">{escape(stats_html)}</p>
  <dl>{facts_html}</dl>
  {warnings_html}
  {verdict}
  {cards}
  {skipped_html}
</main>
</body>
</html>
"""
