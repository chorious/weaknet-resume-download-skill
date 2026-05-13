---
name: weaknet-resume-download
description: Download HuggingFace models from networks where the proxy is slow/expensive but the CDN is directly reachable (e.g. GFW). Resolves CAS bridge URLs via proxy, hands them to a local aria2c RPC daemon for direct multi-connection download, and **rotates the URL via aria2.changeUri before its AWS-presigned signature expires** so multi-hour downloads don't restart. TRIGGER when the user: (a) names a HuggingFace repo id (`owner/name`) AND mentions a proxy / GFW / slow network / weak network / 弱网 / 代理, OR (b) reports an HF download that dies after roughly an hour, OR (c) asks to resume a stalled multi-GB HF model download. SKIP for non-HuggingFace downloads, for HF downloads on normal unrestricted networks (`huggingface-cli` is fine there), or for tiny non-LFS file fetches.
---

# weaknet-resume-download

## What this does that `huggingface-cli` / `hf_transfer` do not

There is **one specific situation** this tool is built for:

- You can reach `huggingface.co` only through a proxy (slow, GFW, paid)
- But `transfer.xethub.hf.co` / `cdn-lfs.huggingface.co` are reachable **directly** and fast
- The CAS bridge URL is an **AWS-presigned URL** that expires roughly 1 hour after issue
- Your model is multi-GB and the download takes longer than 1 hour

Standard tools either route everything through the proxy (slow and expensive) or refuse to refresh URLs mid-download (so they fail at the 1-hour mark). This tool:

1. Resolves `huggingface.co/.../resolve/...` **through the proxy** to get the presigned CAS URL
2. Hands that URL to a local `aria2c --enable-rpc` daemon, which downloads **directly** (no proxy)
3. Parses `X-Amz-Date + X-Amz-Expires` from the URL → knows exactly when it dies
4. Before expiry (default 10 minutes lead), re-resolves through the proxy and calls **`aria2.changeUri`** — aria2 transparently uses the new URL for subsequent range requests; **in-flight connections are not restarted**

Belt-and-suspenders: even if expiry can't be parsed, the engine also refreshes every 50 minutes on a wall clock.

## When to use

YES:
- "下载 owner/foo-GGUF 但我用 v2ray 代理"
- "huggingface 下载到一半断了" + 上下文有代理
- "我要下个大模型，公司网络只能走代理"
- 用户明确提 GFW / 弱网 / 代理 / proxy + HuggingFace

NO:
- 正常无墙网络下载 HF → 用官方 `huggingface-cli`，更简单
- 非 HF 的通用下载 → 直接 `curl` / `aria2c`
- 单个小文件（<100MB）→ 不值得这套机制

## Architecture

```
weaknet-dl ───── httpx ────▶ huggingface.co       (proxied: SOCKS5)
   │                              │
   │                              ▼ returns presigned CAS URL
   │
   └── aria2 RPC ── aria2c ──▶ transfer.xethub.hf.co   (direct)
            │
            └─ aria2.changeUri(gid, old, new) every ~50 min
```

## Install

```bash
pip install -e .

# Required: aria2c (the tool refuses to run without it)
#   Windows: winget install aria2.aria2
#   Debian:  sudo apt install aria2
#   macOS:   brew install aria2
```

## Common invocations

```bash
# Through SOCKS5 proxy (typical use)
weaknet-dl download bartowski/Qwen2.5-7B-Instruct-GGUF ./models/qwen \
    --proxy socks5://127.0.0.1:10808 \
    --include 'Q4_K_M\.gguf$'

# Use env vars instead of flags
WEAKNET_PROXY=socks5://127.0.0.1:10808  HF_TOKEN=hf_xxx \
    weaknet-dl download owner/repo ./out

# Force an early refresh to test the rotation path
weaknet-dl download owner/repo ./out --proxy ... --refresh-lead 3540

# Batch
weaknet-dl batch repos.example.yaml

# Resume / check status
weaknet-dl status ./models/qwen
```

## Environment variables

| Var | Effect |
|---|---|
| `HF_TOKEN` | HuggingFace auth token (gated / private repos) |
| `WEAKNET_PROXY` | Default `--proxy` value |
| `WEAKNET_ARIA2` | Path to `aria2c` binary if not in PATH |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All files downloaded and verified |
| 1 | Partial — some failed; see `<dir>/failed.txt` |
| 2 | Fatal — aria2c missing, auth, network, or config error |

## Behaviour you can rely on

- **Resume:** aria2 keeps `<file>.aria2` control files. Re-running picks up byte-exact where it stopped.
- **Verification:** the HF `tree` API returns LFS `oid` (sha256). After each file finishes, the file is streamed through sha256 and compared. Mismatch → retry.
- **Manifest state:** `<dir>/.weaknet-dl/manifest.json` records verified files. Re-runs skip them.
- **Per-file budget:** `--max-retries` (default 20). On exhaustion, file goes to `failed.txt`, the run continues.
- **Stuck detection:** no `completedLength` growth for `--stuck-timeout` seconds (default 120) → abort gid → outer retry resumes with fresh URL.
- **No sudo:** the deprecated bash version embedded a hardcoded sudo password; this tool never invokes sudo. Auto network recovery is intentionally not implemented (cross-platform safe).
- **No leaked secrets:** the aria2 RPC secret is `secrets.token_urlsafe(24)` per session, bound to `127.0.0.1` only, never written to disk.
