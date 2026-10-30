# Facebook Marketplace Match Bot

Describe what you want in plain English. The bot searches Facebook Marketplace near you, looks through
every listing it finds, and tells you which one is the best match and why.

```
python -m marketplace_bot search "Electric standing desk, at least 60 inches wide, no wobble, under $250, within 25 miles of Denver, CO"
```

## How it works

1. **Understands your prompt.** Claude turns your request into a search plan: several search phrasings
   (brand names, synonyms, common misspellings), your location and distance, your budget, and your
   must-haves, nice-to-haves and deal-breakers.
2. **Searches thoroughly.** It runs every search phrasing on Marketplace in a real browser, scrolls through
   the results and merges duplicates.
3. **Enforces your distance.** It looks up each listing's town and drops anything farther than the radius
   you set. It also drops listings over budget or with titles like "WANTED" or "parts only".
4. **Screens every listing.** Claude scores each remaining listing from its title, price and location,
   so the most promising ones are inspected first.
5. **Inspects the best ones in depth.** It opens each shortlisted listing, reads the full description,
   and has Claude look at the photos. Each listing is checked against every requirement, with notes on
   red flags, scam signs, price fairness, and questions to ask the seller.
6. **Picks the winner.** Claude compares the finalists and picks the best one. The result is a report
   that opens in your browser, plus `matches.csv` for a spreadsheet and `results.json`.

## What you need

- **Your own computer** (Windows, Mac or Linux) with **Python 3.10 or newer**. The bot logs in to Facebook
  as you, so it has to run on your machine.
- **An Anthropic API key** from <https://console.anthropic.com/settings/keys>. This is what powers the
  understanding and matching. Usage is billed to that account (see [Cost](#cost)).
- **A Facebook account** that can use Marketplace.

## One-time setup

```bash
# 1. Get the code
git clone https://github.com/StrawzyCode/FacebookMarketplaceBot.git
cd FacebookMarketplaceBot

# 2. Create a private Python environment and install
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Mac / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# 3. Add your settings
#    Windows: copy .env.example .env      Mac / Linux: cp .env.example .env
#    Then open .env and set ANTHROPIC_API_KEY (and DEFAULT_LOCATION if you like).

# 4. Log in to Facebook once (a browser window opens; log in like normal, including 2FA)
python -m marketplace_bot login
```

Your Facebook login stays in the `.fb_profile` folder on your computer. The bot never sees your
password. Delete that folder to log the bot out.

## Running a search

Activate the environment first (`.venv\Scripts\activate` or `source .venv/bin/activate`), then:

```bash
python -m marketplace_bot search "2016-2019 Toyota Tacoma 4x4, under 120k miles, clean title, no rust, under $28,000, within 75 miles of 78701"
```

Long prompts are easier to keep in a text file:

```bash
python -m marketplace_bot search --prompt-file my_search.txt
```

Use `--plan-only` to check how your prompt was understood before running a full search. It only takes
a few seconds:

```bash
python -m marketplace_bot search "..." --plan-only
```

### Writing a good prompt

- **Say where and how far:** "within 30 miles of Austin, TX" or "within 20 miles of 90210". If you leave it
  out, `DEFAULT_LOCATION` and `DEFAULT_RADIUS_MILES` from `.env` are used.
- **Give your budget:** "under $400" or "between $1,000 and $1,500".
- **List what must be true:** size, model or year range, condition, color, included accessories.
- **List deal-breakers:** "no water damage", "not a salvage title", "no pet hair".
- **Add nice-to-haves** separately so they raise a listing's score without excluding others.
- **Recent listings only:** "posted in the last week".

### Options

| Option | What it does |
| --- | --- |
| `--location "Tampa, FL"` | Search around this place, overriding the prompt |
| `--radius 40` | Max distance in miles, overriding the prompt |
| `--min-price` / `--max-price` | Price limits, overriding the prompt |
| `--max-deep 60` | How many listings to open and inspect in depth (default 40) |
| `--deep-all` | Inspect every listing in range (most thorough, costs the most) |
| `--plan-only` | Show how the prompt was understood, then stop |
| `--headless` | Hide the browser window |
| `--out folder` | Where to save results (default: `results/<date>_<search>`) |
| `--no-open` | Don't open the report automatically |
| `-v` | Show detailed progress logs |

Defaults for most of these can be set in `.env` (see `.env.example`).

## How distance works

Facebook shows only a town for each listing, so the bot measures from the center of your location to the
center of the listing's town, using free OpenStreetMap lookups (cached in `.cache/`). Anything beyond your
radius is dropped. Distances are approximate at the edges of the radius.

The bot also asks Facebook to search the right area. If you see a note that most listings were outside your
radius, Facebook is searching around a different place. Fix it by setting your location on Marketplace
(facebook.com/marketplace → Location), or by putting your city's Marketplace ID in `FB_LOCATION_ID` in
`.env`. To find the ID, pick your city on Marketplace, then click a category: the URL becomes
`facebook.com/marketplace/<ID>/...`.

## Cost

Each search uses Claude (`claude-opus-5-5` by default). With the default settings (up to 40 listings
inspected in depth with 3 photos each), expect roughly **$1–3 per search**. To spend less:

- Lower `MAX_DEEP_CHECKS` or `IMAGES_PER_LISTING` in `.env`, or pass `--max-deep 15`.
- Set `CLAUDE_MODEL=claude-sonnet-5-5` in `.env` (about half the price per token).

The token count for each run is printed at the end.

If Claude's safety filters ever decline a request (very unlikely for shopping), the bot automatically
retries on a fallback model (`CLAUDE_REFUSAL_FALLBACK=true`). If you switch `CLAUDE_MODEL` to a model that
doesn't support fallbacks, set it to `false`.

## Good to know

- **Facebook's rules.** Facebook's terms don't allow automated access. The bot browses slowly with a real
  browser, like a person would, but heavy use could get your account rate-limited or flagged. Use it
  sensibly, at your own risk.
- **Facebook changes its pages.** If searches suddenly return 0 listings while Marketplace works fine in your
  normal browser, Facebook has probably changed its page layout and the bot needs an update. If Facebook keeps
  logging the bot out, run `python -m marketplace_bot login` again, or set `BROWSER_CHANNEL=chrome` to use
  your installed Google Chrome.
- **Keep secrets out of git.** `.env` (your API key), `.fb_profile/` (your Facebook session) and `results/`
  are already in `.gitignore`.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The code lives in `marketplace_bot/`:

| File | Purpose |
| --- | --- |
| `cli.py` | Command-line commands and output |
| `pipeline.py` | The search funnel (plan → search → filter → screen → inspect → pick) |
| `ai.py` | Prompts and calls to Claude (structured outputs) |
| `scraper.py` | Browser automation for Marketplace (Playwright) |
| `geo.py` | Geocoding and distance |
| `parsing.py` | Reading prices, titles and locations from page text |
| `report.py` | HTML / CSV / JSON results |
