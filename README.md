# weaknet-resume-download-skill

Resilient HuggingFace model downloader for weak / flaky networks. Cross-platform Python (Windows / Linux / macOS).

Replaces the original bash scripts. The old bash version had three problems:
1. Linux-only (depended on `bash`, `pgrep`, `systemctl`)
2. Hardcoded sudo password committed in `net-monitor.sh` (now removed)
3. No SHA256 verification, no per-file retry budget, no batch config

## Features

- **Two backends**: `hf` (huggingface_hub + optional `hf_transfer` Rust accelerator) and `aria2` (subprocess wrapper with stuck-detection for very flaky links)
- **Resume**: native to both backends
- **SHA256 verification**: uses the LFS `oid` from HF API; mismatched files retry
- **Manifest state** in `<dir>/.weaknet-dl/manifest.json` — incremental re-runs
- **Per-file retry budget**: bad files go to `failed.txt`, the run continues
- **Batch mode**: `repos.yaml` lists many repos
- **Connectivity probe**: distinguishes DNS / direct / proxy failures (warn-only, no auto recovery — cross-platform safe)

## Install

```bash
pip install -e .

# Optional: Rust-accelerated multi-connection downloads (hf backend)
pip install hf_transfer
# then set HF_HUB_ENABLE_HF_TRANSFER=1

# Optional: aria2c (for --backend aria2)
#   Windows: winget install aria2.aria2
#   Debian:  sudo apt install aria2
#   macOS:   brew install aria2
```

## Quick usage

```bash
# Single repo, default hf backend
weaknet-dl download bartowski/Qwen2.5-7B-Instruct-GGUF ./models/qwen \
    --include 'Q4_K_M\.gguf$'

# Through a SOCKS5 proxy (e.g. local v2ray on 10808)
weaknet-dl download owner/repo ./out --proxy socks5://127.0.0.1:10808

# Use aria2 for maximum parallelism on flaky links
weaknet-dl download owner/repo ./out --backend aria2 --connections 16

# Batch: download many repos
weaknet-dl batch repos.example.yaml

# Check progress in an existing target dir
weaknet-dl status ./models/qwen

# Probe connectivity (one-shot JSON)
weaknet-dl netmon --target huggingface.co --proxy socks5://127.0.0.1:10808 --once
```

## Environment variables

| Var | Effect |
|---|---|
| `HF_TOKEN` | HuggingFace auth token (gated / private repos) |
| `WEAKNET_PROXY` | Default `--proxy` value |
| `WEAKNET_BACKEND` | `hf` or `aria2`, default `hf` |
| `HF_HUB_ENABLE_HF_TRANSFER` | Set to `1` for Rust accelerator (requires `pip install hf_transfer`) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All files downloaded and verified |
| 1 | Partial — some failed; see `<dir>/failed.txt` |
| 2 | Fatal — auth / network / config error before any work started |

## Claude Code skill

`SKILL.md` in the repo root makes this installable as a Claude Code skill. Place this directory under `~/.claude/skills/` (or symlink) and Claude will auto-invoke it on HF-download requests.

## Legacy bash scripts

The original `aria2-hf-dl.sh`, `robust-dl.sh`, `monitor-dl.sh`, `net-monitor.sh` are gone. The repository was rebuilt from scratch with no git history of the bash scripts. **If you cloned the repo before this rewrite, delete that clone — `net-monitor.sh` embedded a plaintext sudo password and any local copy still carries it.**

## Security note

This tool never asks for sudo and never invokes `sudo` internally. Auto network-adapter recovery is intentionally not implemented because it requires privilege escalation that varies per OS; `weaknet-dl netmon` only diagnoses and reports.
