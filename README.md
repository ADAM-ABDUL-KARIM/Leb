# Lebanese Legal Database Scraper

Scrapes Lebanese court rulings and legal articles for the
"circumstances → articles → rulings" pipeline.

## What this gives you

A **working architecture** for scraping two sources (public sites + PU library
database), with Arabic-aware extraction, French detection, retry/resume logic,
and dual output (raw HTML archive + structured JSON).

The architecture works end-to-end RIGHT NOW for any source — but selectors and
URL patterns are placeholders. You'll fill them in once you've inspected the
actual sites you're scraping.

## Project structure

```
lebanese_legal_scraper/
├── core.py                 # source-agnostic engine (don't edit)
├── scrape.py               # main entry point
├── diagnose.py             # selector discovery tool — USE THIS FIRST
├── build_dataset.py        # post-processing into training triples
├── sources/
│   ├── public.py           # public Lebanese sites — TODOs to fill in
│   └── pu.py               # PU library database — TODOs to fill in
└── README.md
```

## Installation

```bash
pip install requests beautifulsoup4 lxml pandas
```

## Workflow (start to finish)

### Step 1 — Pick ONE public source to start with


- `https://www.legallaw.ul.edu.lb/`
- `http://lp.gov.lb/`
- `https://www.legiliban.ul.edu.lb/`
- `https://www.cassation.gov.lb/`

Confirm it's accessible and has rulings before going further. Check
`<site>/robots.txt` to make sure scraping isn't disallowed.

### Step 2 — Run the diagnostic tool on one ruling page

```bash
python diagnose.py --url https://example.lb/ruling/12345 --save-html
```

This will print suggested CSS selectors for court name, judge, date. It also
saves the raw HTML to `./diagnostic_output/page.html` so you can open it in
a browser and inspect manually.

### Step 3 — Fill in `sources/public.py`

Open `sources/public.py`. There are **5 TODOs**. Paste the selectors from
step 2 into the right places. The most critical ones:

- `BASE_URL` — the site's domain
- `discover_ruling_urls()` — how the site lists rulings
- `parse_ruling_html()` — CSS selectors for each field

### Step 4 — Test with 5 rulings

```bash
python scrape.py --source public --max-rulings 5 --verbose
```

Look at `data/public/structured/*.json`. Check:
- Is `case_description` actually the facts section, or did it grab the wrong text?
- Are `cited_articles` complete?
- Is `judge_names` populated?
- Is `has_french` correctly flagged?

Iterate on the parser until the 5 test rulings look right.

### Step 5 — Scale up gradually

```bash
python scrape.py --source public --max-rulings 100        # check
python scrape.py --source public --max-rulings 500 --resume  # check again
python scrape.py --source public --max-rulings 2000 --resume
```

If the connection drops, just rerun with `--resume`. Already-scraped rulings
are skipped.

### Step 6 — Repeat for PU database

```bash
export PU_USERNAME="..."
export PU_PASSWORD="..."

python diagnose.py --url <PU_ruling_url>
# Fill in sources/pu.py based on diagnostic output

python scrape.py --source pu --max-rulings 5 --verbose
# Inspect, iterate, scale
```

### Step 7 — Build the training dataset

```bash
python build_dataset.py --input ./data --output ./training_data
```

Outputs `training_data/triples.csv` and `triples.jsonl` ready for model training.
Also prints summary stats (judge count, court diversity, year range) — these
are what will care about most.

## Output structure

```
data/
├── public/
│   ├── raw/                # raw HTML files
│   ├── structured/         # parsed JSON files
│   └── articles/           # if articles are scraped separately
└── pu/
    ├── raw/
    ├── structured/
    └── articles/
```

## Ethics rules



2. **Public sites**: check `robots.txt` before scraping. Use `User-Agent`
   that identifies you. Keep request delay at 2+ seconds.

3. **Don't parallelize**: one scraper at a time per source. Running multiple
   instances looks like a DoS attack to small servers.

## What's NOT included (handle later)

- Judge name disambiguation (same judge, different spellings)
- Article text deduplication (same article in different citation formats)
- Cross-referencing rulings ↔ article texts
- Quality scoring / flagging suspect extractions
- Translation/normalization of names

These come after you have a working scrape with real data.

## Questions to confirm with 

Before scraping at scale:
1. Which exact databases did she clear for scraping?
2. Volume limit — is 100 rulings ok? 1000? 10000?
3. Should articles be scraped separately, or only as cited in rulings?
4. Does she want a specific year range or court coverage target?
