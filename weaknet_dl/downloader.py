from __future__ import annotations

import time
from pathlib import Path
from typing import List

from .backends import get_backend
from .config import Config
from .hf_api import HFFile, list_files
from .manifest import Manifest
from .verify import verify


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cfg: Config) -> int:
    """Return: 0=all ok, 1=partial, 2=fatal."""
    Path(cfg.local_dir).mkdir(parents=True, exist_ok=True)
    manifest = Manifest.load(cfg.local_dir)
    manifest.repo_id = cfg.repo_id
    manifest.revision = cfg.revision

    _log(f"=== weaknet-dl ===")
    _log(f"repo={cfg.repo_id} dir={cfg.local_dir} backend={cfg.backend} proxy={cfg.proxy or '-'}")

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

    backend = get_backend(cfg.backend)
    failed: list[tuple[str, str]] = []
    succeeded = 0

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
            res = backend.download_one(cfg, f, str(dest))
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
                _log(f"  VERIFIED")
                succeeded += 1
                break
            last_err = f"verify failed: {reason}"
            _log(f"  attempt {attempt}/{cfg.max_retries}: {last_err}")
            manifest.files[f.path].last_error = last_err
            manifest.save(cfg.local_dir)
        else:
            failed.append((f.path, last_err))

    if failed:
        fpath = Path(cfg.local_dir) / "failed.txt"
        with open(fpath, "w", encoding="utf-8") as fh:
            for name, err in failed:
                fh.write(f"{name}\t{err}\n")
        _log(f"DONE: {succeeded}/{len(files)} ok, {len(failed)} failed (see {fpath})")
        return 1

    _log(f"DONE: {succeeded}/{len(files)} all ok")
    return 0
