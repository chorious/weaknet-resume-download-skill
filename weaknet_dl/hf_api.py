from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import httpx

from .config import LARGE_EXTS


@dataclass
class HFFile:
    path: str
    size: int
    sha256: Optional[str]
    is_lfs: bool


def _client(proxy: Optional[str], token: Optional[str]) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kwargs = {"timeout": 60.0, "headers": headers, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def _walk_tree(client: httpx.Client, repo_id: str, revision: str, subpath: str = "") -> Iterator[dict]:
    """Walk HF /api/models/{repo}/tree/{revision} recursively (one level per call)."""
    url = f"https://huggingface.co/api/models/{repo_id}/tree/{revision}"
    if subpath:
        url = f"{url}/{subpath}"
    cursor: Optional[str] = None
    while True:
        params = {"cursor": cursor} if cursor else None
        r = client.get(url, params=params)
        r.raise_for_status()
        entries = r.json()
        if not entries:
            break
        for e in entries:
            if e.get("type") == "directory":
                yield from _walk_tree(client, repo_id, revision, e["path"])
            else:
                yield e
        link = r.headers.get("link", "")
        m = re.search(r'cursor=([^&>"]+)[^>]*>;\s*rel="next"', link)
        if not m:
            break
        cursor = m.group(1)


def list_files(
    repo_id: str,
    revision: str = "main",
    proxy: Optional[str] = None,
    token: Optional[str] = None,
    include_regex: Optional[str] = None,
    exclude_regex: Optional[str] = None,
) -> Iterator[HFFile]:
    inc = re.compile(include_regex) if include_regex else None
    exc = re.compile(exclude_regex) if exclude_regex else None
    with _client(proxy, token) as c:
        for e in _walk_tree(c, repo_id, revision):
            fname = e.get("path", "")
            if not fname or Path(fname).name.startswith("."):
                continue
            if inc and not inc.search(fname):
                continue
            if exc and exc.search(fname):
                continue
            lfs = e.get("lfs") if isinstance(e.get("lfs"), dict) else None
            size = int(e.get("size") or (lfs or {}).get("size") or 0)
            sha = (lfs or {}).get("oid") if lfs else None
            is_lfs = bool(lfs) or fname.lower().endswith(LARGE_EXTS)
            yield HFFile(path=fname, size=size, sha256=sha, is_lfs=is_lfs)


def resolve_url(
    repo_id: str,
    fname: str,
    revision: str = "main",
    proxy: Optional[str] = None,
    token: Optional[str] = None,
) -> str:
    quoted = "/".join(urllib.parse.quote(p) for p in fname.split("/"))
    url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{quoted}"
    with _client(proxy, token) as c:
        r = c.head(url)
        if r.status_code >= 400:
            r = c.get(url, headers={"Range": "bytes=0-0"})
        r.raise_for_status()
        return str(r.url)
