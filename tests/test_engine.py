"""Engine tests with a mocked aria2p API.

These tests don't require aria2c to be installed — they verify the engine's
control flow (poll loop, refresh trigger, stuck detection) by driving a
mock api object.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from weaknet_dl.config import Config
from weaknet_dl.engine import Engine, _Track
from weaknet_dl.hf_api import HFFile, Resolved


def _cfg(tmp_path, **over):
    base = dict(
        repo_id="owner/repo",
        local_dir=str(tmp_path),
        connections=4,
        stuck_timeout=60,
        refresh_lead_seconds=600,
        refresh_interval_seconds=3000,
    )
    base.update(over)
    return Config(**base)


def _make_engine(cfg):
    api = MagicMock()
    return Engine(cfg, api=api, proc=MagicMock()), api


def test_download_calls_add_uri_then_polls_to_completion(tmp_path):
    cfg = _cfg(tmp_path)
    eng, api = _make_engine(cfg)

    api.client.add_uri.return_value = "GID1"
    api.client.tell_status.side_effect = [
        {"status": "active", "completedLength": "100", "totalLength": "1000"},
        {"status": "active", "completedLength": "500", "totalLength": "1000"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000"},
    ]

    resolved = Resolved(
        url="https://cdn/x?X-Amz-Date=20260513T100000Z&X-Amz-Expires=3600",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        domain="cdn",
    )
    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    assert res.bytes_written == 1000
    api.client.add_uri.assert_called_once()
    assert api.client.tell_status.call_count == 3
    api.client.remove_download_result.assert_called_once_with("GID1")


def test_aria2_error_status_returns_failure(tmp_path):
    cfg = _cfg(tmp_path)
    eng, api = _make_engine(cfg)
    api.client.add_uri.return_value = "GID2"
    api.client.tell_status.return_value = {
        "status": "error", "completedLength": "0", "totalLength": "1000",
        "errorCode": "1", "errorMessage": "connection refused",
    }
    resolved = Resolved(url="https://cdn/x", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), domain="cdn")
    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")
    assert res.ok is False
    assert "connection refused" in (res.error or "")


def test_refresh_triggered_when_expiry_within_lead(tmp_path):
    cfg = _cfg(tmp_path, refresh_lead_seconds=600)
    eng, api = _make_engine(cfg)

    api.client.add_uri.return_value = "GID3"
    api.client.tell_status.side_effect = [
        {"status": "active", "completedLength": "100", "totalLength": "1000"},
        {"status": "active", "completedLength": "200", "totalLength": "1000"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000"},
    ]

    # First resolve returns a URL about to expire (60s away, less than 600s lead).
    near_expiry = Resolved(
        url="https://cdn/old",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        domain="cdn",
    )
    far_expiry = Resolved(
        url="https://cdn/new",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        domain="cdn",
    )

    with patch.object(eng, "_resolve", side_effect=[near_expiry, far_expiry]), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    # change_uri must have been called with old/new URLs
    api.client.change_uri.assert_called_once()
    call = api.client.change_uri.call_args
    args, _kwargs = call.args, call.kwargs
    # signature: change_uri(gid, file_index, del_uris, add_uris)
    assert args[0] == "GID3"
    assert args[1] == 1
    assert args[2] == ["https://cdn/old"]
    assert args[3] == ["https://cdn/new"]


def test_stuck_detection_aborts_gid(tmp_path):
    cfg = _cfg(tmp_path, stuck_timeout=1)
    eng, api = _make_engine(cfg)
    api.client.add_uri.return_value = "GID4"

    # Always return same completedLength -> stuck
    api.client.tell_status.return_value = {
        "status": "active", "completedLength": "100", "totalLength": "1000",
    }

    resolved = Resolved(url="https://cdn/x", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), domain="cdn")

    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0.05):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is False
    assert "stuck" in (res.error or "")
    api.client.remove.assert_called_once_with("GID4")


def test_periodic_refresh_when_interval_elapses(tmp_path):
    cfg = _cfg(tmp_path, refresh_lead_seconds=10, refresh_interval_seconds=0)
    eng, api = _make_engine(cfg)
    api.client.add_uri.return_value = "GID5"

    api.client.tell_status.side_effect = [
        {"status": "active", "completedLength": "100", "totalLength": "1000"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000"},
    ]

    # Both calls give a far-in-future expiry → refresh_lead alone wouldn't fire.
    # But refresh_interval_seconds=0 forces periodic to always fire.
    far1 = Resolved(url="https://cdn/u1", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), domain="cdn")
    far2 = Resolved(url="https://cdn/u2", expires_at=datetime.now(timezone.utc) + timedelta(hours=1), domain="cdn")

    with patch.object(eng, "_resolve", side_effect=[far1, far2]), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    api.client.change_uri.assert_called_once()
