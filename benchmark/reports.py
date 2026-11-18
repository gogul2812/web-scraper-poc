"""JSON benchmark artifacts + naive multi-site scaling estimates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmark.metrics import BenchmarkMetrics


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "0%"
    return f"{100.0 * part / whole:.1f}%"


def write_duplication_report(
    path: Path,
    *,
    metrics: BenchmarkMetrics,
    unique_pdp_urls: set[str],
) -> None:
    total = metrics.total_pdp_discovery_events
    unique = len(unique_pdp_urls)
    dup = max(0, total - unique)
    payload = {
        "total_urls_discovered": total,
        "unique_urls": unique,
        "duplicate_urls": dup,
        "duplicate_ratio": _pct(dup, total) if total else "0%",
        "category_enqueue_events": metrics.category_enqueue_events,
        "duplicate_category_enqueues": metrics.duplicate_category_enqueues,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_performance_report(
    path: Path,
    *,
    metrics: BenchmarkMetrics,
    products_ok: int,
    products_failed: int,
) -> None:
    crawl_min = metrics.total_crawl_sec() / 60.0
    disc_min = metrics.discovery_duration_sec() / 60.0
    ext_min = metrics.extraction_phase_sec() / 60.0
    ppm = 0.0
    if crawl_min > 0 and metrics.products_processed > 0:
        ppm = metrics.products_processed / crawl_min
    ipm = 0.0
    if crawl_min > 0 and metrics.images_downloaded > 0:
        ipm = metrics.images_downloaded / crawl_min

    payload = {
        "crawl_duration_minutes": round(crawl_min, 4),
        "discovery_duration_minutes": round(disc_min, 4),
        "product_extraction_duration_minutes": round(ext_min, 4),
        "image_download_duration_seconds": round(metrics.image_download_seconds_total, 3),
        "products_processed": metrics.products_processed,
        "products_per_minute": round(ppm, 4),
        "images_downloaded": metrics.images_downloaded,
        "images_per_minute": round(ipm, 4),
        "average_product_time_seconds": round(metrics.average_product_extraction_sec(), 4),
        "average_page_load_seconds": round(metrics.average_page_load_sec(), 4),
        "blocked_pages": metrics.blocked_pages,
        "timeouts": metrics.timeout_events,
        "retry_count": metrics.retry_count,
        "captcha_events": metrics.captcha_hits,
        "products_ok": products_ok,
        "products_failed_or_blocked": products_failed,
        "peak_memory_mb": metrics.peak_memory_mb(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_crawl_statistics(path: Path, metrics: BenchmarkMetrics, extra: dict[str, Any]) -> None:
    payload = {
        "page_load_samples": len(metrics.page_load_times_sec),
        "retry_count": metrics.retry_count,
        "timeouts": metrics.timeout_events,
        "captcha_hits": metrics.captcha_hits,
        "blocked_pages": metrics.blocked_pages,
        "memory_samples": len(metrics.memory_samples_mb),
        "image_download_seconds_total": metrics.image_download_seconds_total,
        **extra,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_scaling_estimates(path: Path, *, site_duration_minutes: float) -> None:
    """
    Rough throughput model: homogeneous sites, embarrassingly parallel crawl jobs,
    ignore coordination overhead (orchestrator, storage).
    """
    d = site_duration_minutes
    if d <= 0:
        d = 1e-6

    def hours(sites: int, workers: int) -> float:
        w = max(1, workers)
        return sites * d / 60.0 / w

    payload = {
        "assumptions": [
            "Single-site duration extrapolated linearly to N sites.",
            "Perfect horizontal scaling with worker count (no shared bottleneck).",
            "Does not include queue lag, DB writes, or cross-site rate-limit coupling.",
        ],
        "baseline_site_duration_minutes": round(d, 4),
        "estimated_hours_sequential_workers_1": {
            "250_sites": round(hours(250, 1), 3),
            "1000_sites": round(hours(1000, 1), 3),
            "2500_sites": round(hours(2500, 1), 3),
        },
        "estimated_hours_with_workers": {
            "250_sites_10_workers": round(hours(250, 10), 3),
            "1000_sites_25_workers": round(hours(1000, 25), 3),
            "2500_sites_50_workers": round(hours(2500, 50), 3),
        },
        "approx_workers_for_target_hours": {
            "250_sites_in_8h": max(1, int((250 * d / 60.0 / 8.0) + 0.999)),
            "1000_sites_in_8h": max(1, int((1000 * d / 60.0 / 8.0) + 0.999)),
            "2500_sites_in_8h": max(1, int((2500 * d / 60.0 / 8.0) + 0.999)),
            "2500_sites_in_24h": max(1, int((2500 * d / 60.0 / 24.0) + 0.999)),
        },
        "infrastructure_notes": [
            "Provision workers ≈ target_parallelism; add headroom for retries and hot shards.",
            "Separate discovery workers from heavy PDP/image workers if CPU-bound decoding dominates.",
            "Central object storage + CDN egress often dominates cost before raw vCPU.",
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_scaling_into_performance(run_dir: Path, site_duration_minutes: float) -> None:
    perf_path = run_dir / "performance_report.json"
    if not perf_path.exists():
        return
    scaling_path = run_dir / "scaling_estimates.json"
    write_scaling_estimates(scaling_path, site_duration_minutes=site_duration_minutes)
    perf = json.loads(perf_path.read_text(encoding="utf-8"))
    scaling = json.loads(scaling_path.read_text(encoding="utf-8"))
    perf["scaling_estimates_summary"] = scaling.get("estimated_hours_sequential_workers_1", {})
    perf_path.write_text(json.dumps(perf, ensure_ascii=False, indent=2), encoding="utf-8")
