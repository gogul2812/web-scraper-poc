"""Write enterprise discovery audit JSON + Excel artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from services.discovery_audit import DiscoveryAuditSession
from utils.logger import get_logger

logger = get_logger()

AUDIT_SUMMARY_COLUMNS = [
    "search_query",
    "site",
    "site_reported_count",
    "listing_pages_visited",
    "listing_records_scanned",
    "unique_products",
    "duplicate_products",
    "duplicate_ratio",
    "successful_product_extractions",
    "failed_product_extractions",
    "blocked_pages",
    "crawl_duration_minutes",
    "crawl_status",
    "discovery_stop_reason",
]


def write_discovery_audit_report(path: Path, audit: DiscoveryAuditSession, extraction: dict[str, int]) -> None:
    payload = {
        "search_query": audit.search_query,
        "site": audit.site,
        "site_reported_result_count": audit.site_reported_result_count,
        "listing_pages_visited": len(audit.visited_listing_pages),
        "listing_records_scanned": audit.scanned_records_counter,
        "valid_product_pages_found": audit.unique_products_counter,
        "unique_products": audit.unique_products_counter,
        "duplicate_products": audit.duplicate_products_counter,
        "duplicate_ratio": audit.duplicate_ratio,
        "crawl_duration_minutes": round(audit.discovery_duration_minutes(), 4),
        "crawl_status": audit.crawl_status,
        "stop_reason": audit.stop_reason,
        "max_search_pages_limit": audit.max_search_pages_limit,
        "extraction_phase": extraction,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote discovery_audit_report.json (%s listing records)", audit.scanned_records_counter)


def write_pagination_report(path: Path, audit: DiscoveryAuditSession) -> None:
    pages_visited = [
        {
            "page": pr.page_number,
            "url": pr.listing_url,
            "records_found": pr.records_found,
            "unique_added": pr.unique_added,
            "duplicates_on_page": pr.duplicates_on_page,
            "cumulative_scanned": pr.cumulative_scanned,
            "status": pr.status,
        }
        for pr in audit.page_records
    ]
    visited_nums = sorted(audit.visited_listing_pages)
    missing: list[int] = []
    if visited_nums:
        lo, hi = visited_nums[0], visited_nums[-1]
        missing = [p for p in range(lo, hi + 1) if p not in audit.visited_listing_pages and p in audit.failed_pages]

    payload = {
        "total_pages_visited": len(audit.visited_listing_pages),
        "total_pages_logged": len(audit.page_records),
        "pages_visited": pages_visited,
        "missing_pages": missing,
        "failed_pages": sorted(set(audit.failed_pages)),
        "captcha_pages": sorted(set(audit.captcha_pages)),
        "max_search_pages_limit": audit.max_search_pages_limit,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote pagination_report.json (%s pages)", len(pages_visited))


def write_duplicate_analysis(path: Path, audit: DiscoveryAuditSession, *, max_sample: int = 500) -> None:
    dup_keys = audit.duplicate_url_keys()
    sample = dup_keys[:max_sample]
    payload = {
        "total_duplicate_listing_hits": audit.duplicate_products_counter,
        "total_records_scanned": audit.scanned_records_counter,
        "unique_products": audit.unique_products_counter,
        "duplicate_ratio": audit.duplicate_ratio,
        "duplicate_url_keys_count": len(dup_keys),
        "duplicate_urls_sample": sample,
        "most_repeated_products": audit.most_repeated_products(top_n=100),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote duplicate_analysis.json (dup hits=%s)", audit.duplicate_products_counter)


def write_crawl_statistics(
    path: Path,
    audit: DiscoveryAuditSession,
    extraction: dict[str, int],
    *,
    pipeline_duration_minutes: float | None = None,
) -> None:
    payload = {
        "discovery": audit.to_summary_dict(),
        "extraction": extraction,
        "pagination_coverage_percent": _pagination_coverage_pct(audit),
        "pipeline_duration_minutes": pipeline_duration_minutes,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote crawl_statistics.json")


def _pagination_coverage_pct(audit: DiscoveryAuditSession) -> float | None:
    if not audit.site_reported_result_count or audit.site_reported_result_count <= 0:
        return None
    return round(100.0 * audit.scanned_records_counter / audit.site_reported_result_count, 2)


def write_audit_summary_xlsx(
    run_root: Path,
    rows: list[dict[str, Any]],
    excel_subdir: str = "excel",
) -> Path:
    excel_dir = run_root / excel_subdir
    excel_dir.mkdir(parents=True, exist_ok=True)
    path = excel_dir / "audit_summary.xlsx"
    df = pd.DataFrame(rows if rows else [{}])
    for c in AUDIT_SUMMARY_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[AUDIT_SUMMARY_COLUMNS]
    df.to_excel(path, index=False, sheet_name="audit")
    logger.info("Wrote audit_summary.xlsx (%s row(s))", len(rows))
    return path


def write_all_discovery_audit_artifacts(
    site_folder: Path,
    audit: DiscoveryAuditSession,
    extraction: dict[str, int],
    *,
    pipeline_duration_minutes: float | None = None,
) -> None:
    """Persist full audit bundle under the site output folder."""
    write_discovery_audit_report(site_folder / "discovery_audit_report.json", audit, extraction)
    write_pagination_report(site_folder / "pagination_report.json", audit)
    write_duplicate_analysis(site_folder / "duplicate_analysis.json", audit)
    write_crawl_statistics(
        site_folder / "crawl_statistics.json",
        audit,
        extraction,
        pipeline_duration_minutes=pipeline_duration_minutes,
    )


def audit_summary_row(
    audit: DiscoveryAuditSession,
    extraction: dict[str, int],
    *,
    pipeline_duration_minutes: float | None = None,
) -> dict[str, Any]:
    return {
        "search_query": audit.search_query,
        "site": audit.site,
        "site_reported_count": audit.site_reported_result_count or "",
        "listing_pages_visited": len(audit.visited_listing_pages),
        "listing_records_scanned": audit.scanned_records_counter,
        "unique_products": audit.unique_products_counter,
        "duplicate_products": audit.duplicate_products_counter,
        "duplicate_ratio": audit.duplicate_ratio,
        "successful_product_extractions": extraction.get("ok", 0),
        "failed_product_extractions": extraction.get("errors", 0) + extraction.get("filtered", 0),
        "blocked_pages": extraction.get("blocked", 0),
        "crawl_duration_minutes": round(
            pipeline_duration_minutes if pipeline_duration_minutes is not None else audit.discovery_duration_minutes(),
            4,
        ),
        "crawl_status": audit.crawl_status,
        "discovery_stop_reason": audit.stop_reason,
    }
