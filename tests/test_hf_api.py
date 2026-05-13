"""Test resolve_url returns a Resolved with parsed expiry."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from weaknet_dl.hf_api import resolve_url


def test_resolve_url_returns_resolved_with_parsed_expiry():
    fake_url = (
        "https://transfer.xethub.hf.co/blob/abc"
        "?X-Amz-Date=20260513T100000Z&X-Amz-Expires=3600&X-Amz-Signature=x"
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = fake_url
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.head.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)

    with patch("weaknet_dl.hf_api._client", return_value=mock_client):
        r = resolve_url("owner/repo", "model.safetensors")

    assert r.url == fake_url
    assert r.domain == "transfer.xethub.hf.co"
    assert r.expires_at == datetime(2026, 5, 13, 11, 0, 0, tzinfo=timezone.utc)
