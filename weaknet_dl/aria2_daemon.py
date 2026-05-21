"""Spawn and control a local aria2c JSON-RPC daemon.

Design choices:
- The RPC secret is generated per session via `secrets.token_urlsafe(24)`. It
  lives in process memory only — never written to disk, never logged.
- The RPC socket binds to ``127.0.0.1`` so the secret is not exposed beyond
  the local machine.
- Shutdown is graceful (``aria2.shutdown`` RPC) with a hard ``terminate()``
  fallback if the daemon ignores the request.
"""
from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from typing import Optional, Tuple

import aria2p

from .config import Config


class Aria2DaemonError(RuntimeError):
    pass


def _build_args(cfg: Config, rpc_secret: str) -> list[str]:
    args = [
        cfg.aria2_path,
        "--enable-rpc=true",
        f"--rpc-listen-port={cfg.rpc_port}",
        f"--rpc-secret={rpc_secret}",
        "--rpc-listen-all=false",
        "--rpc-allow-origin-all=false",
        # Resilient single-file behaviour
        "--continue=true",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--file-allocation=none",
        f"--max-connection-per-server={cfg.connections}",
        f"--split={cfg.connections}",
        "--min-split-size=16M",
        "--max-concurrent-downloads=1",
        "--max-tries=3",
        "--retry-wait=10",
        "--timeout=60",
        "--connect-timeout=30",
        "--console-log-level=warn",
        "--summary-interval=0",
    ]
    if cfg.user_agent:
        # CDNs (CloudFront, modelscope's nginx) sometimes block or rate-limit
        # default aria2/x.y UAs. Spoofing to a real-browser UA neutralizes that.
        args.append(f"--user-agent={cfg.user_agent}")
    if cfg.aria2_proxy:
        # Route the actual file bytes through a proxy. This changes the
        # source IP seen by the CDN, which is the documented bypass for
        # CloudFront/xethub IP-based rate limiting.
        args.append(f"--all-proxy={cfg.aria2_proxy}")
    return args


def start_daemon(cfg: Config) -> Tuple[subprocess.Popen, aria2p.API, str]:
    """Launch aria2c with RPC. Returns (proc, ready API, rpc_secret).

    Raises:
        Aria2DaemonError: aria2c not found or daemon did not become ready within 15s.
    """
    if shutil.which(cfg.aria2_path) is None:
        raise Aria2DaemonError(
            f"aria2c not found at '{cfg.aria2_path}'. "
            "Install: `winget install aria2.aria2` / `apt install aria2` / `brew install aria2`, "
            "or pass --aria2-path <full path>."
        )

    rpc_secret = secrets.token_urlsafe(24)
    args = _build_args(cfg, rpc_secret)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    client = aria2p.Client(host="http://127.0.0.1", port=cfg.rpc_port, secret=rpc_secret)
    api = aria2p.API(client)

    deadline = time.time() + 15.0
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = (proc.stderr.read() or b"").decode("utf-8", errors="replace") if proc.stderr else ""
            raise Aria2DaemonError(f"aria2c exited early (rc={proc.returncode}): {stderr.strip()}")
        try:
            api.get_global_options()
            return proc, api, rpc_secret
        except Exception:
            time.sleep(0.3)

    proc.terminate()
    raise Aria2DaemonError("aria2c RPC did not become ready within 15s")


def stop_daemon(proc: subprocess.Popen, api: Optional[aria2p.API]) -> None:
    """Try graceful aria2.shutdown then fall back to terminate / kill."""
    if api is not None:
        try:
            api.client.shutdown()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
