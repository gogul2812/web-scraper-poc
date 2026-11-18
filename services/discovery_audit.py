"""
Layer 1 — discovery audit: count every listing record, page visit, and duplicate.

Layer 2 — unique product storage: canonical URLs / ASINs for extraction queue only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "0%"
    return f"{100.0 * part / whole:.2f}%"


def _canon_product_key(url: str) -> str:
    """Normalize PDP URL for uniqueness (ASIN-level on Amazon)."""
    u = (url or "").strip().split("#")[0].split("?")[0].rstrip("/")
    return u.lower()


@dataclass
class PageAuditRecord:
    page_number: int
    listing_url: str
    records_found: int = 0
    unique_added: int = 0
    duplicates_on_page: int = 0
    cumulative_scanned: int = 0
    status: str = "ok"  # ok | failed | captcha | empty


@dataclass
class DiscoveryAuditSession:
    """
    Mandatory counters for auditable marketplace discovery.

    Layer 1: ``scanned_records_counter``, ``duplicate_products_counter``, page logs — no filtering.
    Layer 2: ``unique_product_keys`` — normalized keys for extraction queue.
    """

    site: str = ""
    search_query: str = ""
    marketplace_root: str = ""

    visited_listing_pages: set[int] = field(default_factory=set)
    scanned_records_counter: int = 0
    unique_products_counter: int = 0
    duplicate_products_counter: int = 0

    _unique_keys: set[str] = field(default_factory=set)
    _first_url_by_key: dict[str, str] = field(default_factory=dict)
    _url_hit_counts: dict[str, int] = field(default_factory=dict)

    page_records: list[PageAuditRecord] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    captcha_pages: list[int] = field(default_factory=list)

    site_reported_result_count: int | None = None
    stop_reason: str = ""
    crawl_status: str = "IN_PROGRESS"

    started_at_monotonic: float = field(default_factory=time.perf_counter)
    finished_at_monotonic: float | None = None
    max_search_pages_limit: int = 0

    def set_site_reported_count(self, count: int | None) -> None:
        if count is not None and count >= 0:
            self.site_reported_result_count = count

    def mark_listing_page_visited(self, page_number: int, listing_url: str) -> None:
        self.visited_listing_pages.add(page_number)

    def ingest_listing_urls(self, urls: list[str]) -> tuple[int, int]:
        """
        Register every discovered product URL on the current SERP page.

        Returns (unique_added_on_page, duplicates_on_page).
        """
        unique_added = 0
        duplicates = 0
        for raw in urls:
            if not raw:
                continue
            self.scanned_records_counter += 1
            key = _canon_product_key(raw)
            self._url_hit_counts[key] = self._url_hit_counts.get(key, 0) + 1

            if key in self._unique_keys:
                self.duplicate_products_counter += 1
                duplicates += 1
            else:
                self._unique_keys.add(key)
                self._first_url_by_key[key] = raw.strip().split("#")[0]
                self.unique_products_counter += 1
                unique_added += 1
        return unique_added, duplicates

    def finish_page(
        self,
        page_number: int,
        listing_url: str,
        *,
        records_found: int,
        unique_added: int,
        duplicates_on_page: int,
        status: str = "ok",
    ) -> None:
        self.page_records.append(
            PageAuditRecord(
                page_number=page_number,
                listing_url=listing_url,
                records_found=records_found,
                unique_added=unique_added,
                duplicates_on_page=duplicates_on_page,
                cumulative_scanned=self.scanned_records_counter,
                status=status,
            ),
        )

    def complete(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason
        self.finished_at_monotonic = time.perf_counter()
        self.crawl_status = "COMPLETED" if stop_reason else "COMPLETED"

    def discovery_duration_minutes(self) -> float:
        end = self.finished_at_monotonic or time.perf_counter()
        return (end - self.started_at_monotonic) / 60.0

    @property
    def duplicate_ratio(self) -> str:
        return _pct(self.duplicate_products_counter, self.scanned_records_counter)

    def get_extraction_urls(self) -> list[str]:
        """Layer 2 — first-seen canonical PDP URL per unique product key."""
        return list(self._first_url_by_key.values())

    def most_repeated_products(self, top_n: int = 50) -> list[dict[str, Any]]:
        ranked = sorted(self._url_hit_counts.items(), key=lambda x: -x[1])
        out: list[dict[str, Any]] = []
        for url, count in ranked:
            if count <= 1:
                break
            out.append({"product_url": url, "listing_occurrences": count})
            if len(out) >= top_n:
                break
        return out

    def duplicate_url_keys(self) -> list[str]:
        return [
            self._first_url_by_key.get(k, k)
            for k, c in self._url_hit_counts.items()
            if c > 1
        ]

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "search_query": self.search_query,
            "site": self.site,
            "site_reported_result_count": self.site_reported_result_count,
            "listing_pages_visited": len(self.visited_listing_pages),
            "listing_records_scanned": self.scanned_records_counter,
            "unique_products": self.unique_products_counter,
            "duplicate_products": self.duplicate_products_counter,
            "duplicate_ratio": self.duplicate_ratio,
            "valid_product_pages_found": self.unique_products_counter,
            "stop_reason": self.stop_reason,
            "crawl_status": self.crawl_status,
            "discovery_duration_minutes": round(self.discovery_duration_minutes(), 4),
        }


@dataclass
class DiscoveryResult:
    """Outcome of an audited discovery pass."""

    audit: DiscoveryAuditSession
    extraction_urls: list[str]

    @property
    def unique_urls(self) -> list[str]:
        return self.extraction_urls
