"""
Factory: map host / site label to concrete scraper (Amazon, Flipkart, Snapdeal, …).
"""

from __future__ import annotations

from scraper.amazon_scraper import AmazonScraper
from scraper.base_scraper import BaseScraper
from scraper.flipkart_scraper import FlipkartScraper
from scraper.snapdeal_scraper import SnapdealScraper
from scraper.seb_scraper import SebScraper
from scraper.generic_scraper import GenericScraper


def get_scraper(domain: str) -> BaseScraper:
    """
    Return a scraper for supported domains. Unknown domains -> None (caller skips).
    `domain` is usually a hostname fragment (e.g. amazon.in, flipkart.com).
    """
    d = domain.lower()
    if "amazon" in d:
        return AmazonScraper()
    if "flipkart" in d:
        return FlipkartScraper()
    if "snapdeal" in d:
        return SnapdealScraper()
    if "seb.fr" in d:
        return SebScraper()
    return GenericScraper()
