from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import Config
from .manifest import Manifest


def _add_dl_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("repo_id", help="HuggingFace repo id, e.g. owner/name")
    p.add_argument("local_dir", help="Local target directory")
    p.add_argument("--proxy", default=None, help="SOCKS5/HTTP proxy for /resolve/ only, e.g. socks5://127.0.0.1:10808")
    p.add_argument("--include", default=None, help="Regex to include filenames")
    p.add_argument("--exclude", default=None, help="Regex to exclude filenames")
    p.add_argument("--revision", default="main", help="Branch / tag / commit (default: main)")
    p.add_argument("--connections", type=int, default=8, help="aria2 connections per file (default 8)")
    p.add_argument("--max-retries", type=int, default=20)
    p.add_argument("--stuck-timeout", type=int, default=120, help="Seconds without progress before abort")
    p.add_argument("--refresh-lead", type=int, default=600, help="Refresh CAS URL if it expires within N seconds (default 600)")
    p.add_argument("--aria2-path", default=None, help="Path to aria2c binary (default: search PATH)")
    p.add_argument("--rpc-port", type=int, default=6800, help="aria2 RPC port (default 6800)")
    p.add_argument("--no-verify", action="store_true", help="Skip SHA256 verification")
    p.add_argument("--dry-run", action="store_true", help="List files only, no download")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="weaknet-dl",
        description="HF downloader: proxy-resolves CAS URLs, hands to aria2c RPC, hot-rotates URLs before expiry.",
    )
    parser.add_argument("--version", action="version", version=f"weaknet-dl {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    dl = sub.add_parser("download", help="Download one repo")
    _add_dl_args(dl)

    bp = sub.add_parser("batch", help="Download many repos from repos.yaml")
    bp.add_argument("yaml_path")

    st = sub.add_parser("status", help="Show manifest summary for a directory")
    st.add_argument("local_dir")

    nm = sub.add_parser("netmon", help="Probe connectivity (warn-only, no auto recovery)")
    nm.add_argument("--target", default="huggingface.co")
    nm.add_argument("--proxy", default=None)
    nm.add_argument("--interval", type=int, default=10)
    nm.add_argument("--once", action="store_true", help="Probe once and exit with diagnostic JSON")

    args = parser.parse_args(argv)

    if args.cmd == "download":
        from . import engine
        cfg = Config.from_env_and_args(args)
        return engine.run(cfg)

    if args.cmd == "batch":
        from .batch import run_batch
        return run_batch(args.yaml_path)

    if args.cmd == "status":
        m = Manifest.load(args.local_dir)
        if not m.files:
            print(f"no manifest in {args.local_dir}/.weaknet-dl/")
            return 0
        verified = sum(1 for r in m.files.values() if r.verified_at)
        print(f"repo={m.repo_id}  revision={m.revision}  files={len(m.files)}  verified={verified}")
        for name, rec in sorted(m.files.items()):
            status = "OK" if rec.verified_at else f"PENDING (attempts={rec.attempts})"
            print(f"  {status:30s}  {name}")
            if rec.last_error and not rec.verified_at:
                print(f"    last_error: {rec.last_error}")
        return 0

    if args.cmd == "netmon":
        from . import netmon
        if args.once:
            r = netmon.probe(args.target, args.proxy)
            print(json.dumps(r, indent=2))
            return 0 if (r["dns"] and (r["via_proxy"] if args.proxy else r["direct"])) else 1
        return netmon.run_loop(args.target, args.proxy, args.interval)

    return 2


if __name__ == "__main__":
    sys.exit(main())
