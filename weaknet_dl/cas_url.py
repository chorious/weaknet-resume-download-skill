"""Parse expiry of HuggingFace CAS bridge presigned URLs.

These URLs are AWS Signature V4 presigned URLs, e.g.:

    https://transfer.xethub.hf.co/<blob>?
        X-Amz-Algorithm=AWS4-HMAC-SHA256
        &X-Amz-Date=20260513T100000Z
        &X-Amz-Expires=3600
        &X-Amz-Signature=...
        ...

Expiry = `X-Amz-Date` + `X-Amz-Expires` seconds.

When the URL lacks these params (e.g. a non-presigned CDN URL), we fall back to
"55 minutes from now" — the user-facing refresh loop will then rely on the
periodic strategy rather than the precise strategy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

FALLBACK_LIFETIME = timedelta(minutes=55)


def parse_expiry(url: str, now: datetime | None = None) -> datetime:
    """Return UTC datetime when the URL stops being valid.

    Falls back to ``(now or utcnow()) + FALLBACK_LIFETIME`` when params are
    missing or malformed.
    """
    now = now or datetime.now(timezone.utc)
    try:
        qs = parse_qs(urlparse(url).query)
        date_str = qs.get("X-Amz-Date", [None])[0]
        expires_str = qs.get("X-Amz-Expires", [None])[0]
        if not date_str or not expires_str:
            return now + FALLBACK_LIFETIME
        issued = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return issued + timedelta(seconds=int(expires_str))
    except (ValueError, KeyError):
        return now + FALLBACK_LIFETIME


def domain_of(url: str) -> str:
    return urlparse(url).hostname or "?"
