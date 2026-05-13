from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from .base import DownloadResult
from ..config import Config
from ..hf_api import HFFile, resolve_url


class Aria2Backend:
    name = "aria2"

    def download_one(self, cfg: Config, f: HFFile, dest_path: str) -> DownloadResult:
        if shutil.which("aria2c") is None:
            return DownloadResult(
                ok=False,
                error="aria2c not found in PATH (install via winget/apt/brew, or use --backend hf)",
            )

        try:
            url = resolve_url(cfg.repo_id, f.path, cfg.revision, cfg.proxy, cfg.hf_token)
        except Exception as e:
            return DownloadResult(ok=False, error=f"resolve failed: {type(e).__name__}: {e}")

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "aria2c",
            "--continue=true",
            "--max-tries=3",
            "--retry-wait=10",
            "--timeout=60",
            "--connect-timeout=30",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--file-allocation=none",
            f"--max-connection-per-server={cfg.connections}",
            f"--split={cfg.connections}",
            "--min-split-size=16M",
            "--console-log-level=warn",
            "--summary-interval=30",
            "--dir", str(dest.parent),
            "--out", dest.name,
            url,
        ]

        size_before = dest.stat().st_size if dest.exists() else 0
        stop_flag = {"stop": False}
        stuck = {"stuck": False}

        def watcher():
            last_size = size_before
            last_change = time.time()
            while not stop_flag["stop"]:
                time.sleep(min(15, cfg.stuck_timeout // 4 or 1))
                cur = dest.stat().st_size if dest.exists() else 0
                if cur != last_size:
                    last_size = cur
                    last_change = time.time()
                elif time.time() - last_change > cfg.stuck_timeout:
                    stuck["stuck"] = True
                    break

        t = threading.Thread(target=watcher, daemon=True)
        t.start()

        try:
            proc = subprocess.Popen(args)
            while proc.poll() is None:
                time.sleep(1)
                if stuck["stuck"]:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    stop_flag["stop"] = True
                    return DownloadResult(ok=False, error=f"stuck (no growth for {cfg.stuck_timeout}s)")
            rc = proc.returncode
        finally:
            stop_flag["stop"] = True

        size_after = dest.stat().st_size if dest.exists() else 0
        if rc != 0:
            return DownloadResult(ok=False, bytes_written=size_after - size_before, error=f"aria2c exit={rc}")
        return DownloadResult(ok=True, bytes_written=size_after - size_before)
