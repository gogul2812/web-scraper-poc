"""
Enterprise Excel outputs: master index + failed products.

Legacy index.xlsx helpers removed in favour of excel/master_index.xlsx.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from utils.logger import get_logger

logger = get_logger()

# Enterprise master sheet — stakeholder-facing column order.
MASTER_COLUMNS = [
    "extraction_id",
    "extraction_timestamp",
    "status",
    "validation_mode",
    "country",
    "language",
    "site",
    "site_domain",
    "product_name",
    "product_reference",
    "brand",
    "ean",
    "product_url",
    "category",
    "breadcrumbs",
    "price",
    "currency",
    "availability",
    "short_description",
    "specification_count",
    "image_count",
    "main_image_path",
    "seller_count",
    "output_folder_path",
    "html_path",
    "raw_html_path",
    "clean_html_path",
    "json_path",
    "scrape_duration_seconds",
]


def normalize_row_to_master(
    row: dict[str, Any],
    *,
    extraction_run_id: str,
    validation_mode: str,
    seq_index: int | None = None,
) -> dict[str, Any]:
    """Map heterogeneous scraper rows onto MASTER_COLUMNS."""
    url = row.get("product_url") or ""
    ext_id = row.get("extraction_id") or ""
    if not ext_id:
        site = row.get("site") or "unknown"
        suf = f"{seq_index:06d}" if seq_index is not None else abs(hash(url)) % 10**9
        ext_id = f"{extraction_run_id}|{site}|{suf}"

    ts = (
        row.get("extraction_timestamp")
        or row.get("extraction_date")
        or ""
    )
    price_raw = row.get("price")
    if isinstance(price_raw, dict):
        price_disp = str(price_raw.get("amount", "") or "")
        cur = str(price_raw.get("currency", "") or "")
    else:
        price_disp = str(price_raw or "")
        cur = str(row.get("currency") or "")

    spec_count = row.get("specification_count")
    if spec_count is None and isinstance(row.get("specifications"), dict):
        spec_count = len(row["specifications"])

    out = {
        "extraction_id": ext_id,
        "extraction_timestamp": ts,
        "status": row.get("status") or "",
        "validation_mode": row.get("validation_mode") or validation_mode,
        "country": row.get("country") or "",
        "language": row.get("language") or "",
        "site": row.get("site") or "",
        "site_domain": row.get("site_domain") or "",
        "product_name": row.get("product_name") or "",
        "product_reference": row.get("product_reference") or row.get("reference") or "",
        "brand": row.get("brand") or "",
        "ean": row.get("ean") or "",
        "product_url": url,
        "category": row.get("category") or "",
        "breadcrumbs": row.get("breadcrumbs") or "",
        "price": price_disp,
        "currency": cur,
        "availability": row.get("availability") or "",
        "short_description": (row.get("short_description") or "")[:5000],
        "specification_count": int(spec_count or 0),
        "image_count": int(row.get("image_count") or 0),
        "main_image_path": row.get("main_image_path") or "",
        "seller_count": int(row.get("seller_count") or 0),
        "output_folder_path": row.get("output_folder_path") or row.get("output_path") or "",
        "html_path": row.get("html_path") or "",
        "raw_html_path": row.get("raw_html_path") or row.get("html_path") or "",
        "clean_html_path": row.get("clean_html_path") or "",
        "json_path": row.get("json_path") or "",
        "scrape_duration_seconds": float(row.get("scrape_duration_seconds") or 0),
    }
    return out


def _dataframe_master(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows if rows else [{}])
    for c in MASTER_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[MASTER_COLUMNS]


def write_master_index_csv(run_root: Path, rows: list[dict[str, Any]], excel_subdir: str = "excel") -> Path:
    """Same rows as master_index.xlsx — UTF-8 CSV for easy row counts / BI tools."""
    excel_dir = run_root / excel_subdir
    excel_dir.mkdir(parents=True, exist_ok=True)
    path = excel_dir / "master_index.csv"
    df = _dataframe_master(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("Wrote master_index.csv (%s rows): %s", len(rows), path)
    return path


def write_master_index(run_root: Path, rows: list[dict[str, Any]], excel_subdir: str = "excel") -> Path:
    """excel/master_index.xlsx"""
    excel_dir = run_root / excel_subdir
    excel_dir.mkdir(parents=True, exist_ok=True)
    path = excel_dir / "master_index.xlsx"
    _dataframe_master(rows).to_excel(path, index=False, engine="openpyxl")
    logger.info("Wrote master_index.xlsx (%s rows): %s", len(rows), path)
    return path


def write_failed_products(run_root: Path, rows: list[dict[str, Any]], excel_subdir: str = "excel") -> Path | None:
    """excel/failed_products.xlsx — status not OK."""
    failed = [r for r in rows if str(r.get("status", "")).upper() not in ("OK", "")]
    if not failed:
        logger.info("No failed rows — skipped failed_products.xlsx")
        return None
    excel_dir = run_root / excel_subdir
    excel_dir.mkdir(parents=True, exist_ok=True)
    path = excel_dir / "failed_products.xlsx"
    _dataframe_master(failed).to_excel(path, index=False, engine="openpyxl")
    logger.info("Wrote failed_products.xlsx (%s rows): %s", len(failed), path)
    return path


# Back-compat shim for older callers
INDEX_COLUMNS = MASTER_COLUMNS


def write_run_index(run_root: Path, rows: list[dict], excel_subdir: str = "excel") -> None:
    """Root-level index.xlsx snapshot (same columns as master_index)."""
    legacy = run_root / "index.xlsx"
    _dataframe_master(rows).to_excel(legacy, index=False, engine="openpyxl")
    logger.info("Wrote legacy index.xlsx snapshot (%s rows)", len(rows))


def write_coverage_excel(
    run_root: Path,
    coverage: dict[str, Any],
    excel_subdir: str = "excel",
) -> Path:
    """excel/coverage_report.xlsx with key/value metrics and optional discovery_debug tab."""
    excel_dir = run_root / excel_subdir
    excel_dir.mkdir(parents=True, exist_ok=True)
    path = excel_dir / "coverage_report.xlsx"
    rows = [{"metric": k, "value": v} for k, v in coverage.items() if k != "discovery_debug"]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, index=False, sheet_name="summary")
        dd = coverage.get("discovery_debug")
        if isinstance(dd, dict) and dd:
            dbg_rows: list[dict[str, Any]] = []
            for site, payload in dd.items():
                if isinstance(payload, dict):
                    row = {"site": site}
                    row.update(payload)
                    dbg_rows.append(row)
            if dbg_rows:
                pd.DataFrame(dbg_rows).to_excel(xw, index=False, sheet_name="discovery_debug")
    logger.info("Wrote coverage_report.xlsx: %s", path)
    return path
