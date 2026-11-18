"""
Produce a reader-friendly HTML snapshot for archival (not security sanitization).

Removes scripts, styles, and common chrome blocks — heuristic and site-agnostic.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

_REMOVE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "nav",
    "header",
    "footer",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    ".cookie-banner",
    ".cookies",
    "#cookie",
)


def clean_rendered_html(html: str) -> str:
    """Return stripped-down HTML text suitable for long-term storage."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for sel in _REMOVE_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()
    # Drop inline event handlers
    for tag in soup.find_all(True):
        attrs = dict(tag.attrs)
        for a in attrs:
            if a.startswith("on"):
                del tag.attrs[a]
    return soup.prettify()
