"""benchmark_summary.xlsx for stakeholder review."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from benchmark.metrics import BenchmarkMetrics


def write_benchmark_summary_xlsx(
    path: Path,
    *,
    metrics: BenchmarkMetrics,
    unique_urls: int,
    duplicate_urls: int,
    total_discovery_events: int,
    failures: int,
) -> None:
    crawl_min = metrics.total_crawl_sec() / 60.0
    ppm = metrics.products_processed / crawl_min if crawl_min > 0 else 0.0
    dup_ratio = (duplicate_urls / total_discovery_events * 100.0) if total_discovery_events > 0 else 0.0

    row = {
        "total_discovered_url_events": total_discovery_events,
        "unique_urls": unique_urls,
        "duplicate_url_events": duplicate_urls,
        "duplicate_percent": round(dup_ratio, 2),
        "products_processed": metrics.products_processed,
        "images_downloaded": metrics.images_downloaded,
        "average_extraction_seconds": round(metrics.average_product_extraction_sec(), 4),
        "average_page_load_seconds": round(metrics.average_page_load_sec(), 4),
        "crawl_duration_minutes": round(crawl_min, 4),
        "discovery_duration_minutes": round(metrics.discovery_duration_sec() / 60.0, 4),
        "extraction_phase_minutes": round(metrics.extraction_phase_sec() / 60.0, 4),
        "products_per_minute": round(ppm, 4),
        "images_per_minute": round(
            metrics.images_downloaded / crawl_min if crawl_min > 0 else 0.0,
            4,
        ),
        "failures_non_ok": failures,
        "blocked_pages": metrics.blocked_pages,
        "timeouts": metrics.timeout_events,
        "retries": metrics.retry_count,
        "captcha_events": metrics.captcha_hits,
        "image_download_seconds": round(metrics.image_download_seconds_total, 3),
        "peak_memory_mb": metrics.peak_memory_mb(),
        "category_enqueue_events": metrics.category_enqueue_events,
        "duplicate_category_enqueues": metrics.duplicate_category_enqueues,
    }
    df = pd.DataFrame([row])
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, sheet_name="benchmark")
