# Facebook Marketplace Match Bot

Describe what you want in plain English. The bot searches Facebook Marketplace near you, goes through
the listings it finds, and tells you which one is the best match and why.

**It's free.** No subscriptions, no API keys, nothing to pay.

```
python -m marketplace_bot search "Electric standing desk, at least 60 inches wide, no wobble, under $250, within 25 miles of Denver, CO"
```

## How it works

1. **Reads your prompt:** what you want, where, how far, your budget, must-haves and deal-breakers.
2. **Searches Marketplace** in a real browser using your Facebook login, with several phrasings of your
   item, scrolling through all the results.
3. **Enforces your distance.** It looks up each listing's town and drops anything farther than the radius
   you set. It also drops listings over budget and "WANTED" or "parts only" posts.
4. **Opens and checks the most promising listings:** the full description against every requirement,
   with warnings about deal-breakers and scam signs (pay-first requests, prices far below similar
   listings, "text me at...").
5. **Picks the best match** and opens a report in your browser. You also get `matches.csv` for a
   spreadsheet.

There are two free ways it can judge listings:

| | Keyword matching (default) | Local AI (optional, also free) |
| --- | --- | --- |
| Setup | Nothing extra | Install Ollama + download a model (about 3 GB) |
| Speed | Instant | Slower; depends on your computer |
| Understands | Your words, numbers ("at least 60 inches", "under 120k miles"), years, budgets, "no rust" | Meaning: "barely used", model names, brands it knows |
| Looks at photos | No | Yes |
| Needs | Any computer | 8 GB+ of memory (16 GB for the bigger model) |

The bot uses the local AI automatically if it's installed, and keyword matching if not.

## What you need

- **Your own computer** (Windows, Mac or Linux) with **Python 3.10 or newer**
  ([python.org/downloads](https://www.python.org/downloads/); on Windows, tick "Add Python to PATH").
  The bot logs in to Facebook as you, so it runs on your machine.
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

# 3. Log in to Facebook once (a browser window opens; log in like normal, including 2FA)
python -m marketplace_bot login
```

Your Facebook login stays in the `.fb_profile` folder on your computer. The bot never sees your
password. Delete that folder to log the bot out.

Optional: copy `.env.example` to `.env` to set your usual location and distance, so you don't have to
type them every time.

## Smarter matching (free)

[Ollama](https://ollama.com) runs an AI model on your own computer. It's free, works offline, and nothing
you search for leaves your machine.

1. Download and install Ollama from <https://ollama.com/download>, then open it.
2. Download a model (once, about 3 GB):
   ```bash
   ollama pull gemma3:4b
   ```
   With 16 GB or more of memory, `gemma3:12b` is noticeably smarter. Put `OLLAMA_MODEL=gemma3:12b` in `.env`
   if you use it.
3. That's it. The next search prints `Matching with: free local AI on your computer`.

On a computer without a graphics card the AI can take 20–60 seconds per listing, so by default it inspects
the top 15 listings in depth. Change that with `--max-deep` or `MAX_DEEP_CHECKS` in `.env`.

## Running a search

Activate the environment first (`.venv\Scripts\activate` or `source .venv/bin/activate`), then:

```bash
python -m marketplace_bot search "2016-2019 Toyota Tacoma 4x4, under 120k miles, clean title, no rust, under $28,000, within 75 miles of 78701"
```

**Check how your prompt was understood first.** It takes a second:

```bash
python -m marketplace_bot search "..." --plan-only
```

### Writing a good prompt

- **Where and how far:** "within 30 miles of Austin, TX" or "within 20 miles of 90210".
- **Budget:** "under $400", "$1,000-$1,500" or "$300 max".
- **Requirements, separated by commas:** "at least 60 inches wide", "under 120k miles", "2016-2019",
  "clean title", "two controllers".
- **Deal-breakers start with "no":** "no rust", "no pet hair", "no stains".
- **Nice-to-haves:** "would be nice if it has memory presets".
- **Only recent listings:** "posted in the last week".

For full control, especially with keyword matching, write the prompt as labeled lines in a text file and
run `python -m marketplace_bot search --prompt-file my_search.txt`:

```
item: road bike
location: Portland, OR
distance: 30 miles
budget: 800
must have: carbon frame, 56cm
nice to have: Shimano 105
avoid: crashed, cracked
search: cervelo, trek emonda, carbon road bike
```

`search:` adds extra search phrases. Keyword matching only knows a few synonyms, so listing other names
for the item helps it find more.

### Options

| Option | What it does |
| --- | --- |
| `--location "Tampa, FL"` | Search around this place, overriding the prompt |
| `--radius 40` | Max distance in miles, overriding the prompt |
| `--min-price` / `--max-price` | Price limits, overriding the prompt |
| `--max-deep 60` | How many listings to open and check in full |
| `--deep-all` | Check every listing in range (most thorough, slowest) |
| `--plan-only` | Show how the prompt was understood, then stop |
| `--headless` | Hide the browser window |
| `--out folder` | Where to save results (default: `results/<date>_<search>`) |
| `--no-open` | Don't open the report automatically |
| `-v` | Show detailed progress logs |

## How distance works

Facebook shows only a town for each listing, so the bot measures from the center of your location to the
center of the listing's town, using free OpenStreetMap lookups (cached in `.cache/`). Anything beyond your
radius is dropped. Distances are approximate at the edges of the radius.

The bot also asks Facebook to search the right area. If you see a note that most listings were outside your
radius, Facebook is searching around a different place. Fix it by setting your location on Marketplace
(facebook.com/marketplace → Location), or by putting your city's Marketplace ID in `FB_LOCATION_ID` in
`.env`. To find the ID, pick your city on Marketplace, then click a category: the URL becomes
`facebook.com/marketplace/<ID>/...`.

## Good to know

- **Facebook's rules.** Facebook's terms don't allow automated access. The bot browses slowly with a real
  browser, like a person would, but heavy use could get your account rate-limited or flagged. Use it
  sensibly, at your own risk.
- **Facebook changes its pages.** If searches suddenly return 0 listings while Marketplace works fine in your
  normal browser, Facebook has probably changed its page layout and the bot needs an update. If Facebook keeps
  logging the bot out, run `python -m marketplace_bot login` again, or set `BROWSER_CHANNEL=chrome` to use
  your installed Google Chrome.
- **Your data stays on your computer.** `.env`, `.fb_profile/` (your Facebook session) and `results/` are
  in `.gitignore`.
- **Paid option.** If you ever want the most accurate matching, `AI_BACKEND=claude` plus an
  `ANTHROPIC_API_KEY` in `.env` uses Anthropic's Claude instead, at about $1–3 per search. It's off unless
  you turn it on.

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
| `backends.py` | Chooses keyword matching, local AI or Claude |
| `rules.py` | Free keyword matching: prompt parsing and listing checks |
| `ollama_ai.py` | Free local AI through Ollama |
| `ai.py` | Prompts shared by the AI options, and the paid Claude option |
| `scraper.py` | Browser automation for Marketplace (Playwright) |
| `geo.py` | Geocoding and distance |
| `parsing.py` | Reading prices, titles and locations from page text |
| `report.py` | HTML / CSV / JSON results |
