"""Polite random delays between outbound requests (Playwright or HTTP)."""

from __future__ import annotations

import random
import time

from config import ScraperConfig


def pause_between_requests(cfg: ScraperConfig) -> None:
    """Sleep for a random interval in [delay_min_sec, delay_max_sec]."""
    delay = random.uniform(cfg.delay_min_sec, cfg.delay_max_sec)
    time.sleep(delay)
