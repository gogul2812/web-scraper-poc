"""
Non-aggressive browsing helpers: polite pacing, UA rotation, soft bot signals.

No CAPTCHA bypass — detection only (`utils.html_checks`).
"""

from __future__ import annotations

import random

from config import ScraperConfig
from utils.html_checks import page_contains_captcha
from utils.rate_limit import pause_between_requests

# Pool of common desktop Chrome UAs — pick one per browser context for mild fingerprint variance.
USER_AGENT_POOL: tuple[str, ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
)

DEFAULT_USER_AGENT = USER_AGENT_POOL[0]


def random_user_agent() -> str:
    return random.choice(USER_AGENT_POOL)


def respect_delay_between_steps(cfg: ScraperConfig) -> None:
    pause_between_requests(cfg)


def html_indicates_block(html: str) -> bool:
    return page_contains_captcha(html)
