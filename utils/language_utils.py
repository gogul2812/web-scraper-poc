"""Map site labels / domains to Europe-focused locale hints for reporting."""

from __future__ import annotations


def infer_country_language(site_label: str, host_fragment: str = "") -> tuple[str, str]:
    """
    Return (country_iso_hint, language_iso).
    Defaults conservative when unknown.
    """
    s = f"{site_label} {host_fragment}".lower()
    if "seb.fr" in s or site_label == "seb.fr":
        return "FR", "fr"
    if "amazon.de" in s:
        return "DE", "de"
    if "amazon.fr" in s:
        return "FR", "fr"
    if "amazon.in" in s:
        return "IN", "en"
    if "flipkart" in s:
        return "IN", "en"
    if "snapdeal" in s:
        return "IN", "en"
    return "", "en"


def site_domain_for_reports(site_label: str, site_url: str | None = None) -> str:
    """Human-readable site_domain column (marketing path style when useful)."""
    if site_label == "seb.fr":
        return "seb.fr/fr"
    if site_label:
        return site_label
    return site_url or ""
