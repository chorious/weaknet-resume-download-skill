"""Download engine: one aria2 daemon, sequential files, hot URL rotation.

For each HF file:

1. Resolve the `/resolve/` URL through the proxy → CAS bridge URL + expiry.
2. ``aria2.addUri([cas_url])`` → returns gid.
3. Poll ``aria2.tellStatus(gid)`` every poll_interval seconds.
4. Before the CAS URL expires (refresh_lead seconds before, OR every
   refresh_interval seconds, whichever comes first), re-resolve and call
   ``aria2.changeUri(gid, 1, [old_url], [new_url])``. aria2 transparently uses
   the new URL for subsequent range requests; in-flight connections are not
   restarted.
5. **Stall detection has two layers:**
   - Hard stall: ``completedLength`` does not grow for ``stuck_timeout`` seconds.
     Abort the gid; outer retry resumes from the ``.aria2`` control file.
   - Slow stall (rate-limit): rolling 60s average ``downloadSpeed`` drops below
     ``min_speed_threshold``. Trigger a refresh (cheap, often masks transient
     issues) and emit a clear WARN pointing at ``--aria2-proxy`` and
     ``--hf-endpoint`` as the real mitigations. Cooldown prevents spam.
6. Status line every ``status_log_interval`` seconds with %, speed, conn, TTL.
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, List, Optional, Tuple

from .aria2_daemon import Aria2DaemonError, start_daemon, stop_daemon
from .config import Config
from .hf_api import HFFile, Resolved, list_files, resolve_url
from . import ms_api
from .manifest import Manifest
from .verify import verify


POLL_INTERVAL = 2.0
SPEED_WINDOW_SECONDS = 60.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _fmt_speed(bps: float) -> str:
    return _fmt_bytes(int(bps)) + "/s"


@dataclass
class DownloadResult:
    ok: bool
    bytes_written: int = 0
    error: Optional[str] = None


@dataclass
class _Track:
    f: HFFile
    dest: Path
    current_url: str
    expires_at: datetime
    last_refresh: datetime
    started_at: float = 0.0
    last_completed: int = 0
    last_progress_at: float = 0.0
    last_status_log_at: float = 0.0
    last_rate_limit_action_at: float = 0.0
    speed_window: Deque[Tuple[float, int]] = field(default_factory=collections.deque)
    # True once we've switched this download to a ModelScope URL. MS URLs do
    # not expire, so the refresh path becomes a no-op afterwards.
    on_modelscope: bool = False


class Engine:
    """Owns the aria2 daemon and drives one-file-at-a-time downloads."""

    def __init__(self, cfg: Config, api=None, proc=None, clock=None):
        # api/proc/clock injectable for tests; in production we start the daemon ourselves
        self.cfg = cfg
        self._clock = clock or time.time
        if api is None:
            self.proc, self.api, _ = start_daemon(cfg)
            self._owns_daemon = True
        else:
            self.proc = proc
            self.api = api
            self._owns_daemon = False

    def close(self) -> None:
        if self._owns_daemon:
            stop_daemon(self.proc, self.api)

    def download(self, f: HFFile, dest: Path) -> DownloadResult:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved = self._resolve(f)
        except Exception as e:
            return DownloadResult(ok=False, error=f"resolve failed: {type(e).__name__}: {e}")

        _log(f"  CAS {resolved.domain}  expires_at={resolved.expires_at.isoformat()}")

        try:
            gid = self.api.client.add_uri(
                [resolved.url],
                options={"dir": str(dest.parent), "out": dest.name},
            )
        except Exception as e:
            return DownloadResult(ok=False, error=f"aria2 add_uri failed: {type(e).__name__}: {e}")

        now = self._clock()
        track = _Track(
            f=f, dest=dest,
            current_url=resolved.url,
            expires_at=resolved.expires_at,
            last_refresh=_utcnow(),
            started_at=now,
            last_progress_at=now,
            last_status_log_at=now,
        )
        return self._poll_until_done(gid, track)

    def _resolve(self, f: HFFile) -> Resolved:
        return resolve_url(
            self.cfg.repo_id, f.path, self.cfg.revision,
            self.cfg.proxy, self.cfg.hf_token,
            endpoint=self.cfg.hf_endpoint,
            user_agent=self.cfg.user_agent,
        )

    def _resolve_ms(self, f: HFFile) -> Resolved:
        ms_repo = self.cfg.ms_repo_id or self.cfg.repo_id
        return ms_api.resolve_url(
            ms_repo, f.path, self.cfg.ms_revision,
            endpoint=self.cfg.ms_endpoint,
        )

    def _should_refresh(self, t: _Track) -> bool:
        # MS URLs do not expire; skip the rotation dance once we've switched.
        if t.on_modelscope:
            return False
        now = _utcnow()
        if (t.expires_at - now).total_seconds() <= self.cfg.refresh_lead_seconds:
            return True
        if (now - t.last_refresh).total_seconds() >= self.cfg.refresh_interval_seconds:
            return True
        return False

    def _refresh(self, gid: str, t: _Track) -> None:
        try:
            new = self._resolve(t.f)
        except Exception as e:
            _log(f"  refresh: resolve failed, will retry on next tick ({type(e).__name__}: {e})")
            return
        try:
            self.api.client.change_uri(gid, 1, [t.current_url], [new.url])
        except Exception as e:
            _log(f"  refresh: change_uri failed ({type(e).__name__}: {e})")
            return
        _log(f"  refreshed CAS URL ({t.current_url[:60]}... -> {new.url[:60]}...)")
        t.current_url = new.url
        t.expires_at = new.expires_at
        t.last_refresh = _utcnow()

    def _avg_speed_60s(self, t: _Track, now: float) -> float:
        # Drop samples older than SPEED_WINDOW_SECONDS
        while t.speed_window and now - t.speed_window[0][0] > SPEED_WINDOW_SECONDS:
            t.speed_window.popleft()
        if not t.speed_window:
            return 0.0
        return sum(s for _, s in t.speed_window) / len(t.speed_window)

    def _handle_rate_limit(self, gid: str, t: _Track, now: float, avg_bps: float) -> None:
        t.last_rate_limit_action_at = now
        if t.on_modelscope:
            # Already on MS and still slow — refreshing would put us back on
            # HF (bad). Just warn so the user knows; no auto-action available.
            _log(
                f"  ⚠ WARN: avg speed {_fmt_speed(avg_bps)} on modelscope.cn "
                f"(below {_fmt_speed(self.cfg.min_speed_threshold)}). "
                f"MS is also slow; --aria2-proxy may be the only remaining lever."
            )
            return
        _log(
            f"  ⚠ WARN: avg speed {_fmt_speed(avg_bps)} over last 60s "
            f"(below {_fmt_speed(self.cfg.min_speed_threshold)}). "
            f"CloudFront/xethub likely rate-limited this IP."
        )
        # Preferred mitigation: switch to ModelScope (Alibaba Cloud, different
        # origin and IP range entirely). MS URLs don't expire, so once we're
        # switched we stop the refresh cycle.
        if self.cfg.ms_fallback and self._switch_to_modelscope(gid, t):
            return
        _log("         Real bypass = change source IP. Either:")
        _log("           1. --aria2-proxy socks5://127.0.0.1:10808  (route bytes via proxy)")
        _log("           2. --hf-endpoint https://hf-mirror.com     (different origin entirely)")
        _log("           3. Pause 30+ min — token bucket refills.")
        # Refresh URL anyway: cheap, occasionally helps if the issue is signature side
        self._refresh(gid, t)

    def _switch_to_modelscope(self, gid: str, t: _Track) -> bool:
        """Swap the in-flight aria2 download from HF CDN to modelscope.cn.

        Returns True if the switch succeeded. Resume continues byte-exact
        because aria2 keeps the .aria2 control file regardless of which URL
        serves the next Range request.
        """
        ms_repo = self.cfg.ms_repo_id or self.cfg.repo_id
        try:
            new = self._resolve_ms(t.f)
        except Exception as e:
            _log(f"  ms-fallback: resolve failed ({type(e).__name__}: {e})")
            return False
        try:
            self.api.client.change_uri(gid, 1, [t.current_url], [new.url])
        except Exception as e:
            _log(f"  ms-fallback: change_uri failed ({type(e).__name__}: {e})")
            return False
        _log(
            f"  ms-fallback: switched to modelscope.cn (repo={ms_repo}, file={t.f.path})"
        )
        t.current_url = new.url
        t.expires_at = new.expires_at
        t.last_refresh = _utcnow()
        t.on_modelscope = True
        return True

    def _maybe_status_log(self, t: _Track, now: float, completed: int, total: int,
                          speed_bps: int, conns: int) -> None:
        if now - t.last_status_log_at < self.cfg.status_log_interval:
            return
        t.last_status_log_at = now
        pct = (100.0 * completed / total) if total else 0.0
        ttl = (t.expires_at - _utcnow()).total_seconds()
        ttl_min = max(0, int(ttl // 60))
        _log(
            f"  {_fmt_bytes(completed)} / {_fmt_bytes(total)}  ({pct:.1f}%)  "
            f"speed={_fmt_speed(speed_bps)}  conn={conns}  url_ttl={ttl_min}min"
        )

    def _poll_until_done(self, gid: str, t: _Track) -> DownloadResult:
        while True:
            time.sleep(POLL_INTERVAL)
            try:
                status = self.api.client.tell_status(
                    gid,
                    keys=["status", "completedLength", "totalLength",
                          "downloadSpeed", "connections",
                          "errorCode", "errorMessage"],
                )
            except Exception as e:
                return DownloadResult(ok=False, error=f"tell_status failed: {type(e).__name__}: {e}")

            state = status.get("status")
            completed = int(status.get("completedLength") or 0)
            total = int(status.get("totalLength") or 0)
            speed_bps = int(status.get("downloadSpeed") or 0)
            conns = int(status.get("connections") or 0)

            if state == "complete":
                try:
                    self.api.client.remove_download_result(gid)
                except Exception:
                    pass
                return DownloadResult(ok=True, bytes_written=completed)

            if state == "error":
                err = status.get("errorMessage") or f"errorCode={status.get('errorCode')}"
                try:
                    self.api.client.remove_download_result(gid)
                except Exception:
                    pass
                return DownloadResult(ok=False, bytes_written=completed, error=f"aria2: {err}")

            if state == "removed":
                return DownloadResult(ok=False, bytes_written=completed, error="aria2: removed externally")

            now = self._clock()

            # Hard stall: completedLength has not grown at all
            if completed != t.last_completed:
                t.last_completed = completed
                t.last_progress_at = now
            elif now - t.last_progress_at > self.cfg.stuck_timeout:
                try:
                    self.api.client.remove(gid)
                except Exception:
                    pass
                return DownloadResult(
                    ok=False, bytes_written=completed,
                    error=f"stuck (no progress {self.cfg.stuck_timeout}s)",
                )

            # Slow-stall (CDN rate-limit) detection
            t.speed_window.append((now, speed_bps))
            elapsed_since_start = now - t.started_at
            elapsed_since_action = now - t.last_rate_limit_action_at
            if elapsed_since_start >= SPEED_WINDOW_SECONDS \
                    and elapsed_since_action >= self.cfg.rate_limit_cooldown_seconds:
                avg = self._avg_speed_60s(t, now)
                if avg < self.cfg.min_speed_threshold:
                    self._handle_rate_limit(gid, t, now, avg)

            # Periodic status line
            self._maybe_status_log(t, now, completed, total, speed_bps, conns)

            # Time-based / expiry-based URL refresh
            if self._should_refresh(t):
                self._refresh(gid, t)


def run(cfg: Config) -> int:
    """Return: 0=all ok, 1=partial, 2=fatal."""
    Path(cfg.local_dir).mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(cfg.local_dir)
    manifest.repo_id = cfg.repo_id
    manifest.revision = cfg.revision

    _log("=== weaknet-dl ===")
    _log(
        f"repo={cfg.repo_id} dir={cfg.local_dir} proxy={cfg.proxy or '-'} "
        f"endpoint={cfg.hf_endpoint} aria2_proxy={cfg.aria2_proxy or '-'}"
    )

    try:
        files: List[HFFile] = list(list_files(
            cfg.repo_id, cfg.revision, cfg.proxy, cfg.hf_token,
            cfg.include_regex, cfg.exclude_regex,
            endpoint=cfg.hf_endpoint,
            user_agent=cfg.user_agent,
        ))
    except Exception as e:
        _log(f"FATAL: list_files failed: {type(e).__name__}: {e}")
        return 2

    if not files:
        _log("no files matched filters")
        return 0

    _log(f"{len(files)} files matched")
    if cfg.dry_run:
        for f in files:
            print(f"  {f.path}  ({f.size} bytes, lfs={f.is_lfs}, sha={f.sha256 or '-'})")
        return 0

    try:
        engine = Engine(cfg)
    except Aria2DaemonError as e:
        _log(f"FATAL: {e}")
        return 2

    failed: list[tuple[str, str]] = []
    succeeded = 0
    try:
        for f in files:
            dest = Path(cfg.local_dir) / f.path
            sha = f.sha256 if cfg.verify_sha256 else None

            if manifest.is_verified(f.path, f.size, sha) and dest.exists():
                ok, _ = verify(str(dest), f.size, sha)
                if ok:
                    _log(f"SKIP (verified): {f.path}")
                    succeeded += 1
                    continue

            _log(f"-> {f.path}  ({f.size // 1048576}MB, lfs={f.is_lfs})")

            last_err = ""
            for attempt in range(1, cfg.max_retries + 1):
                manifest.record_attempt(f.path)
                res = engine.download(f, dest)
                if not res.ok:
                    last_err = res.error or "unknown"
                    _log(f"  attempt {attempt}/{cfg.max_retries} failed: {last_err}")
                    manifest.files[f.path].last_error = last_err
                    manifest.save(cfg.local_dir)
                    time.sleep(min(30, 5 * attempt))
                    continue

                ok, reason = verify(str(dest), f.size, sha)
                if ok:
                    manifest.mark_verified(f.path, f.size, sha)
                    manifest.save(cfg.local_dir)
                    _log("  VERIFIED")
                    succeeded += 1
                    break
                last_err = f"verify failed: {reason}"
                _log(f"  attempt {attempt}/{cfg.max_retries}: {last_err}")
                manifest.files[f.path].last_error = last_err
                manifest.save(cfg.local_dir)
            else:
                failed.append((f.path, last_err))
    finally:
        engine.close()

    if failed:
        fpath = Path(cfg.local_dir) / "failed.txt"
        with open(fpath, "w", encoding="utf-8") as fh:
            for name, err in failed:
                fh.write(f"{name}\t{err}\n")
        _log(f"DONE: {succeeded}/{len(files)} ok, {len(failed)} failed (see {fpath})")
        return 1

    _log(f"DONE: {succeeded}/{len(files)} all ok")
    return 0
