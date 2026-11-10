"""Tiny HTTP helper (standard library only)."""
import json
import urllib.request
from urllib.parse import urlencode

# Generic client name on purpose: no personal details are sent to third parties.
UA = "jobreach/1.0 (personal job-search tool)"


def fetch(url, *, data=None, headers=None, timeout=20, limit=20_000_000):
    """Return (body_bytes, content_type, final_url). Raises on HTTP/network errors."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    if isinstance(data, dict):
        data = urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit), r.headers.get("Content-Type", ""), r.geturl()


def get_json(url, **kw):
    return json.loads(fetch(url, **kw)[0])
