"""
Benchmark entrypoint — SEB.fr only; instruments discovery + extraction + reports.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import sync_playwright

from benchmark.config import BenchmarkLimits
from benchmark.excel_benchmark import write_benchmark_summary_xlsx
from benchmark.metrics import BenchmarkMetrics
from benchmark.reports import (
    merge_scaling_into_performance,
    write_crawl_statistics,
    write_duplication_report,
    write_performance_report,
)
from benchmark.seb_benchmark_engine import (
    BenchmarkSebScraper,
    discover_urls_benchmark,
    patch_image_download_timing,
)
from config import ScraperConfig, get_config
from utils.anti_blocking import random_user_agent
from utils.folder_manager import (
    create_benchmark_run_folder,
    create_product_folder,
    create_site_folder,
    hostname_to_site_folder,
)
from utils.language_utils import infer_country_language, site_domain_for_reports
from utils.logger import get_logger, setup_logging
from utils.rate_limit import pause_between_requests

logger = get_logger()


def run_seb_benchmark(
    site_url: str,
    cfg: ScraperConfig | None = None,
    limits: BenchmarkLimits | None = None,
    output_dir: str | None = None,
) -> Path:
    """
    Run benchmark crawl for www.seb.fr — writes output/benchmark_run_*/ artifacts.

    Returns path to benchmark run folder.
    """
    cfg = cfg or get_config()
    limits = limits or BenchmarkLimits()
    out = output_dir or cfg.output_dir

    if "seb.fr" not in site_url.lower():
        raise ValueError("Benchmark mode is implemented for seb.fr only.")

    run_path = create_benchmark_run_folder(out)
    log_path = run_path / "logs" / "scraper.log"
    setup_logging(str(log_path))

    metrics = BenchmarkMetrics()
    metrics.start_wall_clock()

    bench_cfg = replace(
        cfg,
        search_query="benchmark",
        # Disable persisted discovery state noise
    )

    site_label = hostname_to_site_folder(site_url)
    site_folder = create_site_folder(run_path, site_label)
    extraction_run_id = str(uuid.uuid4())

    scraper = BenchmarkSebScraper(metrics)
    scraper.prepare(site_url)
    scraper.discovery_state_path = None  # no resume file for benchmark

    restore_images = patch_image_download_timing(metrics)

    locale, tz = ("fr-FR", "Europe/Paris")
    country_hint, lang_hint = infer_country_language(site_label, site_url)

    logger.info("=== BENCHMARK RUN START — site=%s ===", site_url)

    ok_count = 0
    fail_count = 0
    urls: list[str] = []
    unique_set: set[str] = set()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=random_user_agent(),
                    locale=locale,
                    timezone_id=tz,
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                page.set_default_timeout(bench_cfg.navigation_timeout_ms)

                metrics.start_discovery()
                urls, unique_set = discover_urls_benchmark(
                    scraper,
                    page,
                    bench_cfg,
                    metrics,
                    limits,
                )
                metrics.end_discovery()

                logger.info(
                    "Benchmark discovery: %s unique PDP URLs for extraction; events=%s",
                    len(urls),
                    metrics.total_pdp_discovery_events,
                )

                metrics.start_extraction()

                for idx, purl in enumerate(urls):
                    if idx > 0:
                        pause_between_requests(bench_cfg)
                    if idx % max(1, limits.memory_sample_interval) == 0:
                        metrics.sample_memory_mb()

                    product_id = scraper.format_product_id(idx + 1)
                    product_dir = create_product_folder(
                        site_folder,
                        product_id,
                        create_images_subdir=bench_cfg.save_product_images,
                    )
                    t0 = time.perf_counter()
                    row = scraper.scrape_product(page, purl, "benchmark", product_dir, bench_cfg)
                    dt = time.perf_counter() - t0
                    metrics.product_extraction_times_sec.append(dt)
                    metrics.products_processed += 1

                    st = str(row.get("status") or "").upper()
                    if st == "OK":
                        ok_count += 1
                    else:
                        fail_count += 1
                        metrics.extraction_failures += 1
                        metrics.non_ok_status += 1

                    row["country"] = row.get("country") or country_hint
                    row["language"] = row.get("language") or lang_hint
                    row["site_domain"] = row.get("site_domain") or site_domain_for_reports(
                        scraper.site_label,
                        site_url,
                    )
                    row["extraction_id"] = f"{extraction_run_id}|{site_label}|{idx + 1:06d}"

                    logger.info(
                        "Benchmark PDP %s/%s — %s — %s",
                        idx + 1,
                        len(urls),
                        st,
                        purl[:70],
                    )

                metrics.end_extraction()
                context.close()
            finally:
                browser.close()
    finally:
        restore_images()

    metrics.end_wall_clock()

    total_events = metrics.total_pdp_discovery_events
    unique_n = len(unique_set)
    dup_n = max(0, total_events - unique_n)

    write_duplication_report(
        run_path / "duplication_report.json",
        metrics=metrics,
        unique_pdp_urls=unique_set,
    )
    write_performance_report(
        run_path / "performance_report.json",
        metrics=metrics,
        products_ok=ok_count,
        products_failed=fail_count,
    )
    write_crawl_statistics(
        run_path / "crawl_statistics.json",
        metrics,
        {
            "unique_product_urls_extracted": unique_n,
            "total_discovery_events": total_events,
            "listing_phase_duplicate_category_enqueues": metrics.duplicate_category_enqueues,
        },
    )

    site_duration_min = metrics.total_crawl_sec() / 60.0
    merge_scaling_into_performance(run_path, site_duration_minutes=site_duration_min)

    write_benchmark_summary_xlsx(
        run_path / "excel" / "benchmark_summary.xlsx",
        metrics=metrics,
        unique_urls=unique_n,
        duplicate_urls=dup_n,
        total_discovery_events=total_events,
        failures=fail_count,
    )

    logger.info("=== BENCHMARK COMPLETE — folder=%s ===", run_path)
    logger.info(
        "Artifacts: performance_report.json, duplication_report.json, crawl_statistics.json, scaling_estimates.json, excel/benchmark_summary.xlsx",
    )

    return run_path


def cli_main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="SEB.fr benchmark / load test")
    p.add_argument("-s", "--site", default="https://www.seb.fr", help="SEB storefront URL")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--wall-seconds", type=float, default=None)
    p.add_argument("--max-listing-pages", type=int, default=None)
    p.add_argument("--category-revisit-limit", type=int, default=3)
    args = p.parse_args()

    lim = BenchmarkLimits(
        max_wall_seconds=args.wall_seconds,
        max_listing_pages_total=args.max_listing_pages,
        category_revisit_limit=max(1, args.category_revisit_limit),
    )
    path = run_seb_benchmark(args.site, limits=lim, output_dir=args.output_dir)
    print(path)


if __name__ == "__main__":
    cli_main()
