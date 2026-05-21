"""Engine tests with a mocked aria2p API.

These tests don't require aria2c to be installed — they verify the engine's
control flow (poll loop, refresh trigger, stall detection, rate-limit
detection, status logging) by driving a mock api object.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import count
from unittest.mock import MagicMock, patch

from weaknet_dl.config import Config
from weaknet_dl.engine import Engine
from weaknet_dl.hf_api import HFFile, Resolved


def _cfg(tmp_path, **over):
    base = dict(
        repo_id="owner/repo",
        local_dir=str(tmp_path),
        connections=4,
        stuck_timeout=60,
        refresh_lead_seconds=600,
        refresh_interval_seconds=3000,
        min_speed_threshold=50 * 1024,
        rate_limit_cooldown_seconds=300,
        status_log_interval=30,
        # Most legacy tests assume the HF-only behaviour; opt MS fallback off
        # unless a test explicitly enables it.
        ms_fallback=False,
    )
    base.update(over)
    return Config(**base)


def _make_engine(cfg, clock=None):
    api = MagicMock()
    return Engine(cfg, api=api, proc=MagicMock(), clock=clock), api


def _virtual_clock(step=1.0, start=1_000_000.0):
    """Returns a callable that advances by ``step`` seconds each invocation."""
    counter = count()
    def now():
        return start + step * next(counter)
    return now


def test_download_calls_add_uri_then_polls_to_completion(tmp_path):
    cfg = _cfg(tmp_path)
    eng, api = _make_engine(cfg)
    api.client.add_uri.return_value = "GID1"
    api.client.tell_status.side_effect = [
        {"status": "active", "completedLength": "100", "totalLength": "1000",
         "downloadSpeed": "1000000", "connections": "8"},
        {"status": "active", "completedLength": "500", "totalLength": "1000",
         "downloadSpeed": "1000000", "connections": "8"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "downloadSpeed": "0", "connections": "0"},
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
        "downloadSpeed": "0", "connections": "0",
        "errorCode": "1", "errorMessage": "connection refused",
    }
    resolved = Resolved(url="https://cdn/x",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        domain="cdn")
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
        {"status": "active", "completedLength": "100", "totalLength": "1000",
         "downloadSpeed": "1000000", "connections": "8"},
        {"status": "active", "completedLength": "200", "totalLength": "1000",
         "downloadSpeed": "1000000", "connections": "8"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "downloadSpeed": "0", "connections": "0"},
    ]
    near_expiry = Resolved(url="https://cdn/old",
                           expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                           domain="cdn")
    far_expiry = Resolved(url="https://cdn/new",
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                          domain="cdn")
    with patch.object(eng, "_resolve", side_effect=[near_expiry, far_expiry]), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")
    assert res.ok is True
    api.client.change_uri.assert_called_once()
    args = api.client.change_uri.call_args.args
    assert args[0] == "GID3"
    assert args[1] == 1
    assert args[2] == ["https://cdn/old"]
    assert args[3] == ["https://cdn/new"]


def test_stuck_detection_aborts_gid(tmp_path):
    cfg = _cfg(tmp_path, stuck_timeout=1)
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=1.0))
    api.client.add_uri.return_value = "GID4"
    api.client.tell_status.return_value = {
        "status": "active", "completedLength": "100", "totalLength": "1000",
        "downloadSpeed": "0", "connections": "0",
    }
    resolved = Resolved(url="https://cdn/x",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        domain="cdn")
    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
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
        {"status": "active", "completedLength": "100", "totalLength": "1000",
         "downloadSpeed": "1000000", "connections": "8"},
        {"status": "complete", "completedLength": "1000", "totalLength": "1000",
         "downloadSpeed": "0", "connections": "0"},
    ]
    far1 = Resolved(url="https://cdn/u1",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    domain="cdn")
    far2 = Resolved(url="https://cdn/u2",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    domain="cdn")
    with patch.object(eng, "_resolve", side_effect=[far1, far2]), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")
    assert res.ok is True
    api.client.change_uri.assert_called_once()


# --- v0.4 additions ---

def test_low_speed_triggers_rate_limit_warning_and_refresh(tmp_path, capsys):
    """Speed sustained below threshold for 60s+ → warning + refresh (once, cooldown)."""
    cfg = _cfg(tmp_path,
               min_speed_threshold=50 * 1024,
               rate_limit_cooldown_seconds=10_000,  # long cooldown so second trigger doesn't fire
               status_log_interval=10_000)  # silence status spam in this test
    # Virtual clock: each poll advances 5s. After 13 polls = 65s elapsed (>60s window).
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=5.0))
    api.client.add_uri.return_value = "GID_LO"

    # Always low speed (10 KB/s = 10240 bps) but file IS growing slowly.
    samples = [
        {"status": "active",
         "completedLength": str(100 * i),
         "totalLength": "1000000",
         "downloadSpeed": "10240",  # 10 KB/s, below 50 KB/s threshold
         "connections": "2"}
        for i in range(1, 20)
    ]
    samples.append({"status": "complete", "completedLength": "1000000",
                    "totalLength": "1000000", "downloadSpeed": "0", "connections": "0"})
    api.client.tell_status.side_effect = samples

    resolved_seq = [
        Resolved(url=f"https://cdn/u{i}",
                 expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                 domain="cdn")
        for i in range(10)
    ]
    with patch.object(eng, "_resolve", side_effect=resolved_seq), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    # change_uri should have fired exactly once (rate-limit triggered → refresh,
    # then cooldown 10000s suppresses re-trigger)
    assert api.client.change_uri.call_count == 1
    out = capsys.readouterr().out
    assert "WARN" in out
    assert "rate-limit" in out.lower()
    assert "--aria2-proxy" in out
    assert "--hf-endpoint" in out


def test_high_speed_no_rate_limit_warning(tmp_path, capsys):
    """Healthy speed → no rate-limit warning, no auto-refresh."""
    cfg = _cfg(tmp_path,
               min_speed_threshold=50 * 1024,
               rate_limit_cooldown_seconds=300,
               status_log_interval=10_000)
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=5.0))
    api.client.add_uri.return_value = "GID_HI"

    samples = [
        {"status": "active",
         "completedLength": str(500_000 * i),
         "totalLength": "10000000",
         "downloadSpeed": "5000000",  # 5 MB/s
         "connections": "8"}
        for i in range(1, 18)
    ]
    samples.append({"status": "complete", "completedLength": "10000000",
                    "totalLength": "10000000", "downloadSpeed": "0", "connections": "0"})
    api.client.tell_status.side_effect = samples

    resolved = Resolved(url="https://cdn/x",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        domain="cdn")
    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=10000000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    api.client.change_uri.assert_not_called()
    out = capsys.readouterr().out
    assert "WARN" not in out
    assert "rate-limit" not in out.lower()


def test_ms_fallback_switches_url_on_rate_limit(tmp_path, capsys):
    """When ms_fallback is on, sustained low speed switches the in-flight URL to modelscope.cn."""
    cfg = _cfg(tmp_path,
               ms_fallback=True,
               min_speed_threshold=50 * 1024,
               rate_limit_cooldown_seconds=10_000,
               status_log_interval=10_000)
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=5.0))
    api.client.add_uri.return_value = "GID_MS"
    samples = [
        {"status": "active",
         "completedLength": str(100 * i),
         "totalLength": "1000000",
         "downloadSpeed": "10240",
         "connections": "2"}
        for i in range(1, 20)
    ]
    samples.append({"status": "complete", "completedLength": "1000000",
                    "totalLength": "1000000", "downloadSpeed": "0", "connections": "0"})
    api.client.tell_status.side_effect = samples

    hf_resolved = Resolved(
        url="https://cdn.huggingface.co/blob/abc?X-Amz-Signature=x",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        domain="cdn.huggingface.co",
    )
    ms_resolved = Resolved(
        url="https://modelscope.cn/api/v1/models/owner/repo/repo?Revision=master&FilePath=f.bin",
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
        domain="modelscope.cn",
    )
    with patch.object(eng, "_resolve", return_value=hf_resolved), \
         patch.object(eng, "_resolve_ms", return_value=ms_resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    api.client.change_uri.assert_called_once()
    new_url = api.client.change_uri.call_args.args[3][0]
    assert "modelscope.cn" in new_url
    out = capsys.readouterr().out
    assert "ms-fallback" in out
    # The advice block about --aria2-proxy / --hf-endpoint must NOT appear when
    # the fallback succeeded — we already solved the problem.
    assert "--aria2-proxy" not in out
    assert "--hf-endpoint" not in out


def test_ms_fallback_no_double_switch(tmp_path):
    """Once switched to MS, refresh/rate-limit handlers must not switch again."""
    cfg = _cfg(tmp_path,
               ms_fallback=True,
               min_speed_threshold=50 * 1024,
               rate_limit_cooldown_seconds=0,  # allow repeated triggers
               status_log_interval=10_000)
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=5.0))
    api.client.add_uri.return_value = "GID_MS2"
    samples = [
        {"status": "active",
         "completedLength": str(100 * i),
         "totalLength": "1000000",
         "downloadSpeed": "10240",
         "connections": "2"}
        for i in range(1, 30)
    ]
    samples.append({"status": "complete", "completedLength": "1000000",
                    "totalLength": "1000000", "downloadSpeed": "0", "connections": "0"})
    api.client.tell_status.side_effect = samples

    hf_resolved = Resolved(
        url="https://cdn/x",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        domain="cdn",
    )
    ms_resolved = Resolved(
        url="https://modelscope.cn/api/v1/models/owner/repo/repo?Revision=master&FilePath=f.bin",
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
        domain="modelscope.cn",
    )
    with patch.object(eng, "_resolve", return_value=hf_resolved), \
         patch.object(eng, "_resolve_ms", return_value=ms_resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        res = eng.download(HFFile(path="f.bin", size=1000000, sha256=None, is_lfs=True),
                           tmp_path / "f.bin")

    assert res.ok is True
    # Exactly one switch — subsequent rate-limit triggers must not call
    # change_uri again because we're already on MS (URL is permanent).
    assert api.client.change_uri.call_count == 1


def test_status_line_logged_at_interval(tmp_path, capsys):
    """A status line containing 'speed=' should appear every status_log_interval seconds."""
    cfg = _cfg(tmp_path, status_log_interval=10,
               rate_limit_cooldown_seconds=10_000)
    eng, api = _make_engine(cfg, clock=_virtual_clock(step=5.0))
    api.client.add_uri.return_value = "GID_SL"
    samples = [
        {"status": "active", "completedLength": str(100_000 * i),
         "totalLength": "1000000", "downloadSpeed": "200000", "connections": "8"}
        for i in range(1, 11)
    ]
    samples.append({"status": "complete", "completedLength": "1000000",
                    "totalLength": "1000000", "downloadSpeed": "0", "connections": "0"})
    api.client.tell_status.side_effect = samples

    resolved = Resolved(url="https://cdn/x",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                        domain="cdn")
    with patch.object(eng, "_resolve", return_value=resolved), \
         patch("weaknet_dl.engine.POLL_INTERVAL", 0):
        eng.download(HFFile(path="f.bin", size=1000000, sha256=None, is_lfs=True),
                     tmp_path / "f.bin")

    out = capsys.readouterr().out
    # At 5s/poll and 10s interval, we should see multiple status lines
    status_lines = [ln for ln in out.splitlines() if "speed=" in ln and "url_ttl=" in ln]
    assert len(status_lines) >= 3
