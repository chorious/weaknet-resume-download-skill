from datetime import datetime, timedelta, timezone

from weaknet_dl.cas_url import FALLBACK_LIFETIME, domain_of, parse_expiry


def test_parse_real_shape_url():
    url = (
        "https://transfer.xethub.hf.co/blob/abc"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20260513T100000Z"
        "&X-Amz-Expires=3600"
        "&X-Amz-SignedHeaders=host"
        "&X-Amz-Signature=deadbeef"
    )
    expected = datetime(2026, 5, 13, 11, 0, 0, tzinfo=timezone.utc)
    assert parse_expiry(url) == expected


def test_fallback_when_params_missing():
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    url = "https://cdn-lfs.huggingface.co/abc/def?token=foo"
    assert parse_expiry(url, now=now) == now + FALLBACK_LIFETIME


def test_fallback_when_date_malformed():
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    url = "https://cdn/abc?X-Amz-Date=not-a-date&X-Amz-Expires=3600"
    assert parse_expiry(url, now=now) == now + FALLBACK_LIFETIME


def test_fallback_when_expires_non_numeric():
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)
    url = "https://cdn/abc?X-Amz-Date=20260513T100000Z&X-Amz-Expires=oops"
    assert parse_expiry(url, now=now) == now + FALLBACK_LIFETIME


def test_short_expires():
    url = "https://cdn/abc?X-Amz-Date=20260513T100000Z&X-Amz-Expires=60"
    expected = datetime(2026, 5, 13, 10, 1, 0, tzinfo=timezone.utc)
    assert parse_expiry(url) == expected


def test_domain_of():
    assert domain_of("https://transfer.xethub.hf.co/blob/abc?x=1") == "transfer.xethub.hf.co"
    assert domain_of("not-a-url") in {"?", "not-a-url"}  # tolerant
