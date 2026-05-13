# weaknet-resume-download-skill

HuggingFace model downloader for **one specific situation**:
**huggingface.co is slow/expensive through your proxy, but the CDN (`transfer.xethub.hf.co` / `cdn-lfs.huggingface.co`) is reachable directly and fast.**

## What it does that `huggingface-cli` doesn't

1. **Resolves through the proxy, downloads direct.** Only `huggingface.co/.../resolve/...` goes through the SOCKS5/HTTP proxy. The actual file bytes come from the CDN with no proxy.
2. **Rotates the presigned URL mid-download.** HF's CAS bridge URLs are AWS SigV4 presigned with `X-Amz-Expires=3600` — they die after exactly 1 hour. For multi-GB models that's mid-download. This tool parses the expiry, re-resolves before it hits, and calls **`aria2.changeUri`** to swap the URL inside the running download. **No connection restart, no progress lost.**
3. **Belt-and-suspenders timing.** Even if the expiry can't be parsed (malformed URL, future format change), there's a 50-minute periodic refresh on a wall clock.

This is something neither `huggingface-cli`, `hf_transfer`, nor `huggingface-proxy` (the existing Cloudflare-Worker GFW solution) does today.

## Architecture

```
weaknet-dl ─── httpx ──▶ huggingface.co            (proxied)
   │                          │
   │                          ▼ returns presigned CAS URL + expires_at
   │
   └── aria2 RPC ─ aria2c ──▶ transfer.xethub.hf.co  (direct, multi-connection)
            │
            └─ aria2.changeUri(gid, old, new) before expiry (or every 50 min)
```

## Install

```bash
pip install -e .

# Required: aria2c (1.36+)
#   Windows: winget install aria2.aria2
#   Debian:  sudo apt install aria2
#   macOS:   brew install aria2
```

## Usage

```bash
# Typical: SOCKS5 proxy + filter to one quant
weaknet-dl download bartowski/Qwen2.5-7B-Instruct-GGUF ./models/qwen \
    --proxy socks5://127.0.0.1:10808 \
    --include 'Q4_K_M\.gguf$'

# Via env vars
WEAKNET_PROXY=socks5://127.0.0.1:10808 HF_TOKEN=hf_xxx \
    weaknet-dl download owner/repo ./out

# Manually verify the URL-rotation path works: force refresh after ~1 minute
weaknet-dl download owner/repo ./out \
    --proxy socks5://127.0.0.1:10808 \
    --refresh-lead 3540

# Batch
weaknet-dl batch repos.example.yaml

# Status of a previous run (reads .weaknet-dl/manifest.json)
weaknet-dl status ./models/qwen

# Diagnose connectivity (one-shot JSON: dns / direct / via_proxy)
weaknet-dl netmon --target huggingface.co --proxy socks5://127.0.0.1:10808 --once
```

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--proxy` | none | SOCKS5/HTTP proxy for `/resolve/` only |
| `--include REGEX` | none | Match filenames to download |
| `--exclude REGEX` | none | Skip filenames |
| `--connections N` | 8 | aria2 connections per file |
| `--max-retries N` | 20 | Per-file retry budget before logging to `failed.txt` |
| `--stuck-timeout S` | 120 | Seconds of zero progress before aborting current gid |
| `--refresh-lead S` | 600 | Refresh CAS URL if it dies within S seconds |
| `--aria2-path PATH` | `aria2c` | Override if aria2c is not on PATH |
| `--rpc-port N` | 6800 | aria2 RPC listen port |
| `--no-verify` | off | Skip SHA256 verification (not recommended) |
| `--dry-run` | off | List files but don't download |

## Environment variables

| Var | Effect |
|---|---|
| `HF_TOKEN` | HuggingFace auth (gated/private repos) |
| `WEAKNET_PROXY` | Default `--proxy` |
| `WEAKNET_ARIA2` | Default `--aria2-path` |

## Exit codes

- `0` — all files downloaded and verified
- `1` — partial; see `<dir>/failed.txt` for the list
- `2` — fatal: aria2c missing, auth failure, can't list repo

## Guarantees

- **Resume:** aria2's `.aria2` control file + outer retry loop = byte-exact resume on every restart.
- **Verification:** post-download sha256 against the HF LFS `oid`. Mismatch counts as a failed attempt.
- **Idempotent:** `<dir>/.weaknet-dl/manifest.json` records verified files; re-runs skip them.
- **No privilege escalation:** the tool never invokes `sudo`. The aria2 RPC secret is per-session, in-memory only, bound to `127.0.0.1`.

## Tests

```bash
pip install -e ".[test]"
pytest tests/        # all unit tests, no aria2c required
```

## Origins

This repo started life as four bash scripts. One of them (`net-monitor.sh`) committed a plaintext sudo password to git history — a real fully-publicly-leaked credential. The bash version is gone; this rewrite drops sudo entirely, uses cross-platform Python, and adds the actual unique engineering (URL rotation) instead of the speculative two-backend abstraction the first rewrite had.

## Not goals

- Multi-file parallelism (sequential is more predictable for the rotation logic)
- Fallback if `aria2c` is missing (the rotation IS the point; without aria2c the tool refuses to run)
- BitTorrent, generic-URL, or non-HF downloads
- Auto network-adapter recovery (warn-only via `netmon`; OS-level adapter restart needs privilege escalation that's not cross-platform safe)
