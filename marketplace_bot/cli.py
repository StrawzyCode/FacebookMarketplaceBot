"""Command line entry point:  python -m marketplace_bot {login,search} ..."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

import anthropic

from .ai import AIError, MatchAI
from .config import load_settings
from .geo import GeoLocator, nominatim_geocoder
from .models import SearchPlan
from .pipeline import Overrides, plan_search, run_search
from .report import results_folder, write_results
from .scraper import MarketplaceScraper, NotLoggedInError, interactive_login

EXAMPLES = """\
examples:
  python -m marketplace_bot login
  python -m marketplace_bot search "Electric standing desk, at least 60 inches wide, no wobble, under $250, within 25 miles of Denver, CO"
  python -m marketplace_bot search --prompt-file my_search.txt --max-deep 60
  python -m marketplace_bot search "Toyota Tacoma 2016-2019 4x4 under 120k miles, clean title, under $28k near 78701 within 75 miles" --plan-only
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m marketplace_bot",
        description="Search Facebook Marketplace for the listing that best matches your description.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show detailed progress logs")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="open a browser window to log in to Facebook (one-time setup)")

    s = sub.add_parser("search", help="find the best listings for your prompt")
    s.add_argument("prompt", nargs="?", help="what you're looking for, in plain English (quote it)")
    s.add_argument("--prompt-file", type=Path, help="read the prompt from a text file instead")
    s.add_argument("--location", help="search around this place (overrides the prompt)")
    s.add_argument("--radius", type=float, help="max distance in miles (overrides the prompt)")
    s.add_argument("--min-price", type=int, help="minimum price (overrides the prompt)")
    s.add_argument("--max-price", type=int, help="maximum price (overrides the prompt)")
    s.add_argument("--max-deep", type=int, help="how many listings to open and inspect in depth")
    s.add_argument("--deep-all", action="store_true", help="inspect every listing in range (slower, costs more)")
    s.add_argument("--plan-only", action="store_true", help="show how the prompt was understood, then stop")
    s.add_argument("--headless", action="store_true", help="hide the browser window")
    s.add_argument("--out", type=Path, help="folder to write results into")
    s.add_argument("--no-open", action="store_true", help="don't open the report when finished")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()

    if args.command == "login":
        return 0 if interactive_login(settings) else 1

    prompt = _read_prompt(args)
    if not prompt:
        print("Tell me what you're looking for, e.g.:\n  python -m marketplace_bot search \"...\"", file=sys.stderr)
        return 2

    ai = MatchAI(anthropic.Anthropic(), settings.model, settings.refusal_fallback)
    try:
        return _search(args, prompt, settings, ai)
    except NotLoggedInError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        print("\nYour Anthropic API key was rejected. Check ANTHROPIC_API_KEY in your .env file.", file=sys.stderr)
        return 2
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            print("\nNo Anthropic API key found. Put ANTHROPIC_API_KEY=... in your .env file.", file=sys.stderr)
            return 2
        raise
    except (anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
        print(f"\nThe Anthropic API refused the request ({exc}). Check CLAUDE_MODEL and your account.", file=sys.stderr)
        return 2
    except (AIError, ValueError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


def _read_prompt(args) -> str:
    if args.prompt_file:
        return args.prompt_file.read_text(encoding="utf-8").strip()
    return (args.prompt or "").strip()


def _search(args, prompt: str, settings, ai: MatchAI) -> int:
    overrides = Overrides(args.location, args.radius, args.min_price, args.max_price)
    print("Understanding your request...")
    plan = plan_search(ai, prompt, settings, overrides)
    _print_plan(plan)
    if args.plan_only:
        return 0
    if not plan.location:
        print(
            "\nNo location found. Say where in your prompt (\"within 30 miles of Austin, TX\"), "
            "pass --location, or set DEFAULT_LOCATION in .env.",
            file=sys.stderr,
        )
        return 2

    deep_limit = 10_000 if args.deep_all else args.max_deep
    geo = GeoLocator(settings.cache_dir / "geocode.json", nominatim_geocoder(settings.geocoder_user_agent))
    print("\nOpening Facebook Marketplace...")
    with MarketplaceScraper(settings, headless=True if args.headless else None) as scraper:
        result = run_search(prompt, plan, settings, scraper, ai, geo, deep_limit=deep_limit)

    out_dir = args.out or results_folder(settings.results_dir, plan.summary)
    paths = write_results(result, out_dir)
    _print_summary(result, paths, ai)
    if not args.no_open:
        webbrowser.open(paths["html"].resolve().as_uri())
    return 0


def _print_plan(plan: SearchPlan) -> None:
    where = plan.location or "(no location)"
    if plan.radius_miles:
        where += f", within {plan.radius_miles:g} miles"
    print(f"\n  Looking for:   {plan.summary}")
    print(f"  Where:         {where}")
    print(f"  Price:         {plan.budget_text()}")
    print(f"  Searches:      {', '.join(plan.queries)}")
    if plan.must_haves:
        print(f"  Must have:     {'; '.join(plan.must_haves)}")
    if plan.nice_to_haves:
        print(f"  Nice to have:  {'; '.join(plan.nice_to_haves)}")
    if plan.deal_breakers:
        print(f"  Deal-breakers: {'; '.join(plan.deal_breakers)}")


def _print_summary(result, paths, ai: MatchAI) -> None:
    print()
    for w in result.warnings:
        print(f"Note: {w}")
    if result.best:
        b = result.best
        dist = f"{b.distance_miles:.0f} mi" if b.distance_miles is not None else "distance unknown"
        print(f"\nBEST MATCH ({b.evaluation.match_score}/100): {b.title}")
        print(f"  {b.price_text or 'price not shown'} · {dist} · {b.location}")
        print(f"  {b.url}")
        print(f"  {result.explanation}")
    elif result.ranked:
        print("\nNo listing fully matched. Closest ones are in the report.")
    else:
        print("\nNo listings could be inspected.")
    runners = [l for l in result.ranked if l is not result.best][:4]
    if runners:
        print("\nAlso worth a look:")
        for l in runners:
            print(f"  {l.evaluation.match_score:>3}/100  {l.price_text or '?':>10}  {l.title[:60]}  {l.url}")
    u = ai.usage
    print(
        f"\nClaude usage: {u.requests} requests, {u.input_tokens + u.cache_read_tokens:,} input tokens "
        f"({u.cache_read_tokens:,} cached), {u.output_tokens:,} output tokens"
    )
    print(f"Report: {paths['html']}")
