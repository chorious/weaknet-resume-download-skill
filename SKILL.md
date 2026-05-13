---
name: weaknet-resume-download
description: Download HuggingFace models on flaky / weak networks with resume, SHA256 verification, and configurable backends. TRIGGER when the user asks to download a HuggingFace repo/model, mentions a `<owner>/<repo>` id pointing at huggingface.co, complains about HF downloads stalling/timing out, asks to resume a partial HF download, or wants to batch-download multiple HF repos. SKIP for non-HuggingFace downloads (use curl/wget/aria2c directly).
---

# weaknet-resume-download

Resilient HuggingFace model downloader for poor network conditions. Cross-platform (Windows / Linux / macOS). Pure Python.

## When to use

Trigger on any of these signals:
- User names a HuggingFace repo id (`owner/name`) and asks to download it
- User has a partially-downloaded HF model and wants to resume
- User reports HF downloads stalling, timing out, or dropping repeatedly
- User wants to download multiple HF repos in batch

Do **not** use for: non-HF downloads, single small files (just use `curl`), or when the user already has a working download in progress.

## Quick decision tree

```
user goal?
├── one repo, normal network  → backend=hf (default), no proxy
├── one repo, behind GFW       → backend=hf, --proxy socks5://127.0.0.1:10808
├── extremely flaky/dropping   → backend=aria2, --proxy ... (aria2c required)
├── many repos                 → write repos.yaml, weaknet-dl batch repos.yaml
└── monitoring an active run   → weaknet-dl status <dir>
```

## Install

```bash
pip install -e .
# optional: enable Rust-accelerated multi-connection downloads
pip install hf_transfer
# optional: install aria2c for the aria2 backend (fallback for very flaky nets)
#   Windows: winget install aria2.aria2
#   Debian:  sudo apt install aria2
#   macOS:   brew install aria2
```

## Common invocations

```bash
# Single repo, default backend
weaknet-dl download bartowski/Qwen2.5-7B-Instruct-GGUF ./models/qwen \
    --include 'Q4_K_M\.gguf$'

# Through SOCKS5 proxy
weaknet-dl download owner/repo ./out --proxy socks5://127.0.0.1:10808

# Force aria2 backend for max parallelism
weaknet-dl download owner/repo ./out --backend aria2 --connections 16

# Batch
weaknet-dl batch repos.yaml

# Resume / check status (reads .weaknet-dl/manifest.json)
weaknet-dl status ./models/qwen

# Network probe (does NOT auto-recover — warns only, cross-platform safe)
weaknet-dl netmon --target huggingface.co --proxy socks5://127.0.0.1:10808
```

## Environment variables

| Var | Effect |
|---|---|
| `HF_TOKEN` | HuggingFace auth token (for gated/private repos) |
| `WEAKNET_PROXY` | Default `--proxy` value |
| `WEAKNET_BACKEND` | `hf` or `aria2`, default `hf` |
| `HF_HUB_ENABLE_HF_TRANSFER` | Set to `1` for Rust-accelerated downloads (requires `hf_transfer`) |

## Exit codes

- `0` — all files downloaded and verified
- `1` — partial: some files failed; see `failed.txt` in target dir
- `2` — fatal: auth / network / config error before any work started

## Design notes

- **State**: `<target>/.weaknet-dl/manifest.json` records per-file size + sha256 + verified_at. Re-running is incremental.
- **Verification**: HF API exposes LFS `oid` (sha256). After download, the file is streamed through sha256 and compared. Mismatch → retry.
- **Retry budget**: per-file `--max-retries` (default 20). On exhaustion, file is logged to `failed.txt` and the next file is attempted. The overall command does not block forever on one bad file.
- **No password prompts**: the deprecated bash `net-monitor.sh` embedded a hardcoded sudo password — that script has been removed. Auto network-recovery is intentionally NOT implemented (cross-platform safe).
