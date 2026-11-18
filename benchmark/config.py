"""Benchmark limits (separate from production ScraperConfig)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkLimits:
    """
    Load-test controls. Relaxed category revisits model repeated discovery in the wild.
    """

    # How many times the same category hub may be enqueued (BFS stress + duplicate tracking).
    category_revisit_limit: int = 3
    max_discovery_queue: int = 8000
    # Optional wall-clock stop (seconds); None = no limit.
    max_wall_seconds: float | None = None
    # Optional cap on total listing pages visited (None = use cfg-driven budget).
    max_listing_pages_total: int | None = None
    # Sample memory every N product scrapes.
    memory_sample_interval: int = 5
