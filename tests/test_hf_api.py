"""Test resolve_url returns a Resolved with parsed expiry; endpoint param honored."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from weaknet_dl.hf_api import resolve_url


def _mock_head_response(final_url):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = final_url
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.head.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=None)
    return mock_client


def test_resolve_url_returns_resolved_with_parsed_expiry():
    fake_url = (
        "https://transfer.xethub.hf.co/blob/abc"
        "?X-Amz-Date=20260513T100000Z&X-Amz-Expires=3600&X-Amz-Signature=x"
    )
    mock_client = _mock_head_response(fake_url)
    with patch("weaknet_dl.hf_api._client", return_value=mock_client):
        r = resolve_url("owner/repo", "model.safetensors")
    assert r.url == fake_url
    assert r.domain == "transfer.xethub.hf.co"
    assert r.expires_at == datetime(2026, 5, 13, 11, 0, 0, tzinfo=timezone.utc)


def test_resolve_url_uses_endpoint_param():
    """When endpoint is set, the resolve URL must point at the mirror, not huggingface.co."""
    mock_client = _mock_head_response("https://transfer.xethub.hf.co/blob/abc")
    with patch("weaknet_dl.hf_api._client", return_value=mock_client):
        resolve_url("owner/repo", "model.bin", endpoint="https://hf-mirror.com")
    requested_url = mock_client.head.call_args.args[0]
    assert requested_url.startswith("https://hf-mirror.com/owner/repo/resolve/main/")
    assert "huggingface.co" not in requested_url


def test_resolve_url_default_endpoint_is_huggingface():
    mock_client = _mock_head_response("https://cdn/blob")
    with patch("weaknet_dl.hf_api._client", return_value=mock_client):
        resolve_url("owner/repo", "model.bin")
    requested_url = mock_client.head.call_args.args[0]
    assert requested_url.startswith("https://huggingface.co/")


def test_resolve_url_passes_user_agent_to_client():
    """The configured UA must be forwarded into the httpx client factory."""
    mock_client = _mock_head_response("https://cdn/blob")
    with patch("weaknet_dl.hf_api._client", return_value=mock_client) as factory:
        resolve_url("owner/repo", "model.bin", user_agent="MyBrowser/1.0")
    # The factory is called as _client(proxy, token, user_agent)
    call = factory.call_args
    assert "MyBrowser/1.0" in call.args or call.kwargs.get("user_agent") == "MyBrowser/1.0"
