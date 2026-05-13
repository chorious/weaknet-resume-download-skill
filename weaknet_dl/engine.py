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
5. Stuck detection: if ``completedLength`` does not grow for stuck_timeout
   seconds, abort the gid and return failure so the outer retry loop can
   resume with a fresh URL.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .aria2_daemon import Aria2DaemonError, start_daemon, stop_daemon
from .config import Config
from .hf_api import HFFile, Resolved, list_files, resolve_url
from .manifest import Manifest
from .verify import verify


POLL_INTERVAL = 2.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    last_completed: int = 0
    last_progress_at: float = 0.0


class Engine:
    """Owns the aria2 daemon and drives one-file-at-a-time downloads."""

    def __init__(self, cfg: Config, api=None, proc=None):
        # api/proc injectable for tests; in production we start the daemon ourselves
        self.cfg = cfg
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

        track = _Track(
            f=f, dest=dest,
            current_url=resolved.url,
            expires_at=resolved.expires_at,
            last_refresh=_utcnow(),
            last_progress_at=time.time(),
        )
        return self._poll_until_done(gid, track)

    def _resolve(self, f: HFFile) -> Resolved:
        return resolve_url(
            self.cfg.repo_id, f.path, self.cfg.revision,
            self.cfg.proxy, self.cfg.hf_token,
        )

    def _should_refresh(self, t: _Track) -> bool:
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

    def _poll_until_done(self, gid: str, t: _Track) -> DownloadResult:
        while True:
            time.sleep(POLL_INTERVAL)
            try:
                status = self.api.client.tell_status(
                    gid,
                    keys=["status", "completedLength", "totalLength", "errorCode", "errorMessage"],
                )
            except Exception as e:
                return DownloadResult(ok=False, error=f"tell_status failed: {type(e).__name__}: {e}")

            state = status.get("status")
            completed = int(status.get("completedLength") or 0)
            total = int(status.get("totalLength") or 0)

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

            now = time.time()
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

            if self._should_refresh(t):
                self._refresh(gid, t)


def run(cfg: Config) -> int:
    """Return: 0=all ok, 1=partial, 2=fatal."""
    Path(cfg.local_dir).mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(cfg.local_dir)
    manifest.repo_id = cfg.repo_id
    manifest.revision = cfg.revision

    _log("=== weaknet-dl ===")
    _log(f"repo={cfg.repo_id} dir={cfg.local_dir} proxy={cfg.proxy or '-'}")

    try:
        files: List[HFFile] = list(list_files(
            cfg.repo_id, cfg.revision, cfg.proxy, cfg.hf_token,
            cfg.include_regex, cfg.exclude_regex,
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
