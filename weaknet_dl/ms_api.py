"""ModelScope (modelscope.cn) metadata + URL construction.

ModelScope is Alibaba's HuggingFace mirror. Many popular HF models are also
published there under the same ``owner/name`` id, and crucially the download
URL is a **stable, non-presigned** endpoint:

    https://modelscope.cn/api/v1/models/{repo_id}/repo?Revision={rev}&FilePath={path}

That URL does not expire, supports Range, and is served from Alibaba Cloud
inside China — exactly what's needed when HF's CloudFront/xethub IP rate-limits
make the primary path unusable.

The file-list API returns entries shaped like:
    {"Path": "...", "Size": int, "Sha256": "<hex>", "IsLFS": bool,
     "Type": "blob"|"tree", "Name": "..."}

ModelScope hosts mirrors on the public internet — these calls deliberately do
*not* go through the HF proxy. The whole point of fallback is "the proxy /
HF CDN is the problem; go direct via .cn instead."
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import httpx

from .config import LARGE_EXTS, MODELSCOPE_ENDPOINT
from .hf_api import HFFile, Resolved


# MS URLs do not expire. We still set a synthetic expiry far in the future so
# the engine's refresh logic stays a no-op for MS-sourced URLs.
_MS_PSEUDO_TTL = timedelta(days=365)


def _client(user_agent: Optional[str] = None) -> httpx.Client:
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    return httpx.Client(timeout=60.0, headers=headers, follow_redirects=True)


def list_files(
    repo_id: str,
    revision: str = "master",
    include_regex: Optional[str] = None,
    exclude_regex: Optional[str] = None,
    endpoint: str = MODELSCOPE_ENDPOINT,
    user_agent: Optional[str] = None,
) -> Iterator[HFFile]:
    """Recursive file list from ModelScope, shaped as HFFile records."""
    inc = re.compile(include_regex) if include_regex else None
    exc = re.compile(exclude_regex) if exclude_regex else None
    base = endpoint.rstrip("/")
    url = f"{base}/api/v1/models/{repo_id}/repo/files"
    params = {"Revision": revision, "Recursive": "true"}
    with _client(user_agent) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        payload = r.json()
    data = payload.get("Data") or {}
    files = data.get("Files") or []
    for entry in files:
        if entry.get("Type") == "tree":
            continue
        fname = entry.get("Path") or entry.get("Name") or ""
        if not fname or Path(fname).name.startswith("."):
            continue
        if inc and not inc.search(fname):
            continue
        if exc and exc.search(fname):
            continue
        size = int(entry.get("Size") or 0)
        sha = entry.get("Sha256")
        is_lfs = bool(entry.get("IsLFS")) or fname.lower().endswith(LARGE_EXTS)
        yield HFFile(path=fname, size=size, sha256=sha, is_lfs=is_lfs)


def resolve_url(
    repo_id: str,
    fname: str,
    revision: str = "master",
    endpoint: str = MODELSCOPE_ENDPOINT,
) -> Resolved:
    """Build the static MS download URL for one file. No network call needed."""
    base = endpoint.rstrip("/")
    quoted_path = urllib.parse.quote_plus(fname)
    quoted_rev = urllib.parse.quote_plus(revision)
    url = f"{base}/api/v1/models/{repo_id}/repo?Revision={quoted_rev}&FilePath={quoted_path}"
    expires_at = datetime.now(timezone.utc) + _MS_PSEUDO_TTL
    domain = urllib.parse.urlparse(base).hostname or "modelscope.cn"
    return Resolved(url=url, expires_at=expires_at, domain=domain)
