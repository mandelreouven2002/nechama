"""Reads the festival context document (Google Docs, shared by link) with a short cache."""
import logging
import re
import time

import requests

log = logging.getLogger(__name__)
_CACHE = {"url": None, "text": None, "at": 0.0}
CACHE_SECONDS = 120
_DOC_ID = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def export_url(doc_url):
    match = _DOC_ID.search(doc_url or "")
    if not match:
        return None
    return f"https://docs.google.com/document/d/{match.group(1)}/export?format=txt"


def get_context_text(config):
    """Return the context document text; fall back to the stored copy on failure."""
    url = export_url(config.context_doc_url)
    if not url:
        return config.context_fallback
    now = time.monotonic()
    if _CACHE["url"] == url and _CACHE["text"] and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["text"]
    try:
        resp = requests.get(url, timeout=15)
        content_type = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and "text/plain" in content_type:
            text = resp.content.decode("utf-8-sig").strip()
            if text:
                _CACHE.update(url=url, text=text, at=now)
                if text != config.context_fallback:
                    type(config).objects.filter(pk=config.pk).update(context_fallback=text)
                return text
        log.warning(
            "Context doc fetch returned %s (%s); is it shared by link?",
            resp.status_code,
            content_type,
        )
    except requests.RequestException as exc:
        log.warning("Context doc fetch failed: %s", exc)
    return config.context_fallback
