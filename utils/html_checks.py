"""Lightweight HTML signal detection (CAPTCHA, bot checks)."""

from __future__ import annotations


def page_contains_captcha(html: str) -> bool:
    """
    Detect real bot walls — avoid matching newsletter/analytics strings that contain
    'recaptcha' or 'captcha' on normal PDPs (e.g. seb.fr newsletter widgets).
    """
    if not html:
        return False
    l = html.lower()
    if "robot check" in l:
        return True
    if "sorry, we just need to make sure you're not a robot" in l:
        return True
    if "enter the characters you see below" in l or "type the characters you see in this image" in l:
        return True
    if "cf-browser-verification" in l or "checking your browser before accessing" in l:
        return True
    if "validatecaptcha" in l or "cvf-page-content" in l:
        return True
    return False
