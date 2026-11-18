# Enterprise product ingestion POC

Production-style pipeline for **large-scale reseller extraction**, built around **Groupe SEB (seb.fr)** with secondary handlers for **Amazon.in**, **Flipkart**, **Snapdeal**, and a **generic** stub for new domains.

**→ Full technical write-up (stack, techniques, anti-blocking, CAPTCHA handling, architecture): [TECHNICAL_DOCUMENTATION.md](./TECHNICAL_DOCUMENTATION.md)**

## Features

- **Discovery** → **PDP extraction** → **validation-ready JSON** → **images** → **reporting**
- Outputs per run: `metadata.json`, `coverage_report.json`, `error_report.json`, `logs/scraper.log`, `excel/master_index.xlsx`, `excel/failed_products.xlsx`, optional root `index.xlsx`
- Per product (SEB): `raw.html`, `clean.html`, `data.json`, `images/` with role-based filenames (`main_01.jpg`, `secondary_01.jpg`, …)
- **Playwright** browser context per site (locale/timezone), **randomised desktop User-Agent** from a safe pool, delays, retries, CAPTCHA **detection** (no bypass)
- **500+ PDP scale** via configurable caps (`max_products`, `seb_max_category_visits`, category pagination)

## Setup

```bash
cd "Web Scrapping POC"
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium
```

## Run

Interactive:

```bash
python main.py
```

Non-interactive — **SEB enterprise crawl** (example 500 products):

```bash
python main.py -y -q "campaign-seb-001" -s "https://www.seb.fr" --max-products 500
```

Multi-site:

```bash
python main.py -y -q "kitchen" -s "https://www.seb.fr,https://www.amazon.in" --max-products 100
```

Optional future EAN validation (loads codes; full enforcement wired via `enforce_product_validation` in `config.py`):

```bash
python main.py -y -q "test" -s "https://www.amazon.in" --ean-dataset ./eans.json --max-products 50
```

## Configuration

Edit **`config.py`**:

| Area | Keys |
|------|------|
| Volume | `max_products` (default 500), `max_search_pages`, `seb_max_category_visits`, `seb_max_pages_per_category` |
| Timing | `delay_min_sec`, `delay_max_sec`, `navigation_timeout_ms` |
| Export | `product_id_padding` (default 6 → `product_000001`), `save_clean_html`, `save_screenshot_on_failure`, `excel_subdir` |
| Validation | `validation_mode`, `ean_dataset_path`, `enforce_product_validation` |

## Output layout

```
output/run_YYYYMMDD_HHMMSS/
  metadata.json
  coverage_report.json
  error_report.json
  logs/scraper.log
  excel/master_index.xlsx
  excel/failed_products.xlsx
  index.xlsx                    # legacy mirror of master columns
  seb.fr/
    product_000001/
      raw.html
      clean.html
      data.json
      images/main_01.jpg
      ...
```

## Architecture (high level)

| Layer | Role |
|-------|------|
| `scraper/` | Site-specific scrapers (`seb_scraper`, `amazon_scraper`, …, `generic_scraper`) |
| `services/` | Discovery wrapper, extraction helpers, Excel, metadata, reporting |
| `utils/` | Logging, retry, rate limits, HTML cleaning, locales, anti-blocking UA pool |

Unknown hosts resolve to **`GenericScraper`** (empty discovery) — replace with a real implementation and register it in `scraper/factory.py`.

## Ethics & compliance

- Respect site terms; use polite delays and retries.
- **No** third-party CAPTCHA-solving or aggressive fingerprint bypass.
- Blocked pages are marked `BLOCKED` and recorded in `error_report.json`.

## Licence / use

Internal / client POC only — ensure crawling is authorised for your environment.
