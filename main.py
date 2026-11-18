"""
Enterprise multi-site ingestion POC — discovery → extraction → validation-ready outputs.

Primary showcase: Groupe SEB (seb.fr). Secondary: Amazon / Flipkart / Snapdeal / generic stub.

Usage:
    pip install -r requirements.txt && playwright install chromium

    Full catalog (e.g. seb.fr — keywords optional):
      python main.py -y -s "https://www.seb.fr"

    Marketplace search (keywords required):
      python main.py -y -q "Samsung S21" -s "https://www.amazon.in"

    Optional hard limit:
      python main.py -y -s "https://www.seb.fr" --max-products 200

Interactive: 
    python main.py
"""

from __future__ import annotations
   
import argparse
import json
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone 
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from config import ScraperConfig, effective_product_cap, get_config
from scraper.factory import get_scraper
from services.discovery_service import discover_products
from services.excel_service import (
    normalize_row_to_master,
    write_coverage_excel,
    write_failed_products,
    write_master_index,
    write_master_index_csv,
    write_run_index,
)
from services.metadata_service import write_enhanced_metadata
from services.discovery_audit_reports import (
    audit_summary_row,
    write_all_discovery_audit_artifacts,
    write_audit_summary_xlsx,
)
from services.reporting_service import (
    aggregate_coverage,
    append_error,
    write_coverage_report,
    write_error_report,
)
from services.validation_service import VALID_EANS, load_ean_dataset_from_file
from utils.anti_blocking import random_user_agent
from utils.folder_manager import (
    create_product_folder,
    create_run_folder,
    create_site_folder,
    ensure_run_logs_dir,
    hostname_to_site_folder,
)
from utils.language_utils import infer_country_language, site_domain_for_reports
from utils.logger import get_logger, setup_logging
from utils.rate_limit import pause_between_requests

logger = get_logger()


def parse_and_normalize_urls(raw: str) -> list[str]:
    out: list[str] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if not p.startswith(("http://", "https://")):
            p = "https://" + p
        u = urlparse(p)
        if not u.netloc:
            continue
        out.append(p)
    return out


def prompt_user(base_cfg: ScraperConfig) -> tuple[str, str, int | None]:
    print()
    print(
        "Enter website URL(s), comma-separated, e.g.\n"
        "  https://www.seb.fr  or  https://www.amazon.in",
    )
    sites_line = input("Websites:\n").strip()
    query = input(
        "Optional keywords for Amazon / Flipkart / Snapdeal (e.g. Samsung S21).\n"
        "Leave blank for full-catalog crawl on sites like seb.fr:\n",
    ).strip()
    tip = (
        "Optional max products per site (Enter = no limit — uses internal safety cap; "
        f"hard ceiling {base_cfg.catalog_discovery_hard_cap}):\n"
    )
    raw_max = input(tip).strip()
    if not raw_max:
        max_products: int | None = None
    else:
        try:
            v = int(raw_max)
            max_products = None if v <= 0 else min(v, base_cfg.catalog_discovery_hard_cap)
        except ValueError:
            max_products = None
    return query, sites_line, max_products


def marketplace_requires_keywords(site_url: str) -> bool:
    """Search-marketplace scrapers need a non-empty query string."""
    key = hostname_to_site_folder(site_url).lower()
    return any(x in key for x in ("amazon", "flipkart", "snapdeal"))


def resolve_cli_max_products(cli_val: int | None, base: ScraperConfig) -> int | None:
    """CLI --max-products: None inherits config; <=0 means unlimited intent."""
    if cli_val is None:
        return base.max_products
    if cli_val <= 0:
        return None
    return min(int(cli_val), base.catalog_discovery_hard_cap)


def locale_for_site(site_url: str) -> tuple[str, str]:
    s = site_url.lower()
    if "seb.fr" in s:
        return "fr-FR", "Europe/Paris"
    return "en-IN", "Asia/Kolkata"


def run_for_site(
    scraper,
    page,
    query: str,
    cfg: ScraperConfig,
    site_path,
    site_url: str,
    extraction_run_id: str,
    validation_mode: str,
    master_rows: list,
    error_bucket: list,
) -> tuple[int, dict[str, int]]:
    urls = discover_products(scraper, page, query, cfg)
    audit = getattr(scraper, "discovery_audit", None)
    if audit is not None:
        logger.info(
            "[%s] Discovery audit — listing_records_scanned=%s unique=%s duplicates=%s "
            "pages_visited=%s site_reported=%s → extraction queue=%s",
            scraper.site_label,
            audit.scanned_records_counter,
            audit.unique_products_counter,
            audit.duplicate_products_counter,
            len(audit.visited_listing_pages),
            audit.site_reported_result_count,
            len(urls),
        )
    else:
        logger.info("[%s] Discovered %s product URL(s) for extraction.", scraper.site_label, len(urls))

    dstats = getattr(scraper, "discovery_stats", None)
    if isinstance(dstats, dict) and dstats.get("stop_reason"):
        logger.info(
            "[%s] Discovery stop_reason=%r — listing_pages_visited=%s listing_records_scanned=%s",
            scraper.site_label,
            dstats.get("stop_reason"),
            dstats.get("listing_pages_visited"),
            dstats.get("listing_records_scanned"),
        )

    extraction_counts: dict[str, int] = {
        "attempted": 0,
        "ok": 0,
        "errors": 0,
        "blocked": 0,
        "filtered": 0,
    }

    if urls:
        pause_between_requests(cfg)

    country_hint, lang_hint = infer_country_language(scraper.site_label, site_url)

    for idx, purl in enumerate(urls):
        extraction_counts["attempted"] += 1
        product_id = scraper.format_product_id(idx + 1)
        if idx > 0:
            pause_between_requests(cfg)
        product_dir = create_product_folder(
            site_path,
            product_id,
            create_images_subdir=cfg.save_product_images,
        )
        t_scrape = time.perf_counter()
        row = scraper.scrape_product(page, purl, query, product_dir, cfg)
        if float(row.get("scrape_duration_seconds") or 0) <= 0:
            row["scrape_duration_seconds"] = round(time.perf_counter() - t_scrape, 3)
        row["country"] = row.get("country") or country_hint
        row["language"] = row.get("language") or lang_hint
        row["site_domain"] = row.get("site_domain") or site_domain_for_reports(
            scraper.site_label,
            site_url,
        )
        row["extraction_id"] = f"{extraction_run_id}|{scraper.site_label}|{idx + 1:06d}"
        row["validation_mode"] = validation_mode

        master_rows.append(
            normalize_row_to_master(
                row,
                extraction_run_id=extraction_run_id,
                validation_mode=validation_mode,
                seq_index=len(master_rows) + 1,
            ),
        )
        st = str(row.get("status") or "").upper()
        if st == "OK":
            extraction_counts["ok"] += 1
        elif st == "BLOCKED":
            extraction_counts["blocked"] += 1
        elif st == "FILTERED":
            extraction_counts["filtered"] += 1
        else:
            extraction_counts["errors"] += 1
        if st != "OK":
            append_error(
                error_bucket,
                site=scraper.site_label,
                product_url=purl,
                failure_type=st or "UNKNOWN",
                detail="",
                status_before=st,
            )
        logger.info(
            "Progress: [%s] %s/%s — %s — %s",
            scraper.site_label,
            idx + 1,
            len(urls),
            row.get("status"),
            purl[:70],
        )

    if audit is not None:
        write_all_discovery_audit_artifacts(site_path, audit, extraction_counts)

    return len(urls), extraction_counts


def _metadata_validation_mode(cfg: ScraperConfig) -> str:
    if VALID_EANS:
        return "dataset"
    return cfg.validation_mode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enterprise ingestion POC — SEB + marketplaces (Playwright + reports).",
    )
    parser.add_argument(
        "-q",
        "--query",
        default="",
        help="Search keywords for marketplace sites; omit for full-catalog sites (e.g. seb.fr)",
    )
    parser.add_argument(
        "-s",
        "--sites",
        help="Comma-separated sites (e.g. https://www.seb.fr,https://www.amazon.in)",
    )
    parser.add_argument(
        "-y",
        "--non-interactive",
        action="store_true",
        help="Require --query and --sites (no prompts)",
    )
    parser.add_argument("--ean-dataset", help="Optional EAN/GTIN list for future dataset validation")
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        metavar="N",
        help="Optional PDP cap per site. Omit or use 0 for full catalog (bounded by catalog_discovery_hard_cap in config).",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="SEB.fr load-test mode: metrics + duplication_report + performance_report (see benchmark/ package).",
    )
    parser.add_argument(
        "--benchmark-wall-seconds",
        type=float,
        default=None,
        help="Optional wall-clock limit for benchmark discovery+extraction phases.",
    )
    parser.add_argument(
        "--benchmark-max-listing-pages",
        type=int,
        default=None,
        help="Optional cap on benchmark listing pages fetched.",
    )
    parser.add_argument(
        "--benchmark-category-revisit-limit",
        type=int,
        default=3,
        help="How often the same category hub may be re-enqueued (stress / duplicate metrics).",
    )
    args = parser.parse_args()

    base_cfg = get_config()

    if args.benchmark:
        if not args.sites:
            print("--benchmark requires --sites https://www.seb.fr", file=sys.stderr)
            sys.exit(1)
        b_urls = parse_and_normalize_urls(args.sites)
        if len(b_urls) != 1:
            print("--benchmark: use exactly one URL (seb.fr).", file=sys.stderr)
            sys.exit(1)
        if "seb.fr" not in b_urls[0].lower():
            print("--benchmark is implemented for www.seb.fr only.", file=sys.stderr)
            sys.exit(1)
        from benchmark.config import BenchmarkLimits
        from benchmark.runner import run_seb_benchmark

        lim = BenchmarkLimits(
            max_wall_seconds=args.benchmark_wall_seconds,
            max_listing_pages_total=args.benchmark_max_listing_pages,
            category_revisit_limit=max(1, int(args.benchmark_category_revisit_limit)),
        )
        cfg_b = replace(
            base_cfg,
            max_products=resolve_cli_max_products(args.max_products, base_cfg),
        )
        out = run_seb_benchmark(b_urls[0], cfg=cfg_b, limits=lim, output_dir=base_cfg.output_dir)
        print(out)
        sys.exit(0)

    if args.sites:
        query = (args.query or "").strip()
        sites_raw = args.sites
        max_products = resolve_cli_max_products(args.max_products, base_cfg)
    elif args.non_interactive:
        print("Non-interactive mode requires --sites.", file=sys.stderr)
        sys.exit(1)
    else:
        query, sites_raw, max_products = prompt_user(base_cfg)

    url_list = parse_and_normalize_urls(sites_raw)
    if not url_list:
        print("No valid website URLs provided.", file=sys.stderr)
        sys.exit(1)

    if not query.strip():
        needs_kw = [u for u in url_list if marketplace_requires_keywords(u)]
        if needs_kw:
            print(
                "Search keywords are required for marketplace sites (Amazon, Flipkart, Snapdeal). "
                "Use --query / -q or run interactive mode.\n"
                f"Affected: {', '.join(needs_kw)}",
                file=sys.stderr,
            )
            sys.exit(1)

    ean_path = args.ean_dataset or base_cfg.ean_dataset_path
    cfg = replace(
        base_cfg,
        search_query=query,
        ean_dataset_path=ean_path,
        max_products=max_products,
    )
    effective_cap = effective_product_cap(cfg)

    extraction_run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    t_pipeline = time.perf_counter()

    run_path = create_run_folder(cfg.output_dir)
    ensure_run_logs_dir(run_path)
    run_log = run_path / "logs" / "scraper.log"
    setup_logging(str(run_log))

    n_ean = load_ean_dataset_from_file(ean_path)
    if n_ean:
        logger.info("EAN dataset active: %s code(s) loaded.", n_ean)

    logger.info(
        "=== Ingestion run start — extraction_run_id=%s — query=%r — max_products/site=%r — effective_cap=%s ===",
        extraction_run_id,
        query,
        cfg.max_products,
        effective_cap,
    )
    if effective_cap >= 200:
        logger.warning(
            "Large crawl: effective product cap %s per site — expect long runtime (delays + PDP + images).",
            effective_cap,
        )

    master_rows: list = []
    error_bucket: list = []
    discovered_urls_total = 0
    validation_mode = _metadata_validation_mode(cfg)
    discovery_debug: dict[str, dict] = {}
    audit_summary_rows: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for site_idx, site_url in enumerate(url_list):
                host_key = hostname_to_site_folder(site_url)
                scraper = get_scraper(host_key)
                scraper.prepare(site_url)
                scraper.product_id_padding = cfg.product_id_padding

                site_folder = create_site_folder(run_path, scraper.site_label)
                setattr(scraper, "discovery_state_path", str((site_folder / "discovery_state.json").resolve()))
                locale, tz = locale_for_site(site_url)
                context = browser.new_context(
                    user_agent=random_user_agent(),
                    locale=locale,
                    timezone_id=tz,
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                page.set_default_timeout(cfg.navigation_timeout_ms)

                logger.info(
                    "--- Site %s/%s: %s (locale=%s) ---",
                    site_idx + 1,
                    len(url_list),
                    scraper.site_label,
                    locale,
                )

                try:
                    n, ext_counts = run_for_site(
                        scraper,
                        page,
                        query,
                        cfg,
                        site_folder,
                        site_url,
                        extraction_run_id,
                        validation_mode,
                        master_rows,
                        error_bucket,
                    )
                    discovered_urls_total += n
                    stats = getattr(scraper, "discovery_stats", None)
                    if isinstance(stats, dict):
                        discovery_debug[scraper.site_label] = stats
                    audit = getattr(scraper, "discovery_audit", None)
                    if audit is not None:
                        audit_summary_rows.append(
                            audit_summary_row(
                                audit,
                                ext_counts,
                                pipeline_duration_minutes=None,
                            ),
                        )
                except Exception as e:
                    logger.exception("Site-level failure for %s: %s", site_url, e)
                finally:
                    context.close()
        finally:
            browser.close()

    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    duration_sec = time.perf_counter() - t_pipeline

    coverage = aggregate_coverage(master_rows, discovered_urls_total)
    coverage["discovered_urls_reported"] = discovered_urls_total
    coverage["total_master_rows"] = len(master_rows)
    if discovery_debug:
        coverage["discovery_debug"] = discovery_debug

    write_coverage_report(run_path, coverage)
    write_error_report(run_path, error_bucket)

    write_enhanced_metadata(
        run_path,
        extraction_run_id=extraction_run_id,
        query=query,
        site_hosts=[hostname_to_site_folder(u) for u in url_list],
        validation_mode=validation_mode,
        max_products_per_site=cfg.max_products,
        catalog_discovery_hard_cap=cfg.catalog_discovery_hard_cap,
        cli_args_summary=" ".join(sys.argv[1:]),
        coverage=coverage,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        duration_seconds=duration_sec,
    )

    write_master_index(run_path, master_rows, excel_subdir=cfg.excel_subdir)
    write_master_index_csv(run_path, master_rows, excel_subdir=cfg.excel_subdir)
    write_failed_products(run_path, master_rows, excel_subdir=cfg.excel_subdir)
    write_coverage_excel(run_path, coverage, excel_subdir=cfg.excel_subdir)
    write_run_index(run_path, master_rows, excel_subdir=cfg.excel_subdir)

    if audit_summary_rows:
        for row in audit_summary_rows:
            row["crawl_duration_minutes"] = round(duration_sec / 60.0, 4)
        write_audit_summary_xlsx(run_path, audit_summary_rows, excel_subdir=cfg.excel_subdir)

    run_summary = {
        "discovered_urls_total": discovered_urls_total,
        "master_index_rows": len(master_rows),
        "rows_match_discovered": len(master_rows) == discovered_urls_total,
        "coverage": coverage,
        "note": (
            "If master_index_rows < discovered_urls_total, the process likely crashed before "
            "finishing extraction — Excel/CSV are only written at the end of a successful run."
        ),
    }
    (run_path / "run_summary.json").write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if len(master_rows) != discovered_urls_total:
        logger.warning(
            "Reconciliation: discovered_urls_total=%s but master_index_rows=%s — "
            "see run_summary.json and error_report.json (possible mid-run failure).",
            discovered_urls_total,
            len(master_rows),
        )
    else:
        logger.info(
            "Reconciliation: %s rows written to master_index (Excel + CSV match discovered URL count).",
            len(master_rows),
        )

    logger.info(
        "=== Run complete — folder: %s — rows: %s — duration: %.1fs ===",
        run_path,
        len(master_rows),
        duration_sec,
    )
    logger.info(
        "Artifacts: metadata.json, coverage_report.json, error_report.json, run_summary.json, "
        "excel/ (+ master_index.csv, audit_summary.xlsx), per-site discovery_audit_*.json",
    )
    logger.info("Log file: %s", run_log)


if __name__ == "__main__":
    main()
