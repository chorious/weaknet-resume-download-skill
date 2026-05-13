from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


def file_sha256(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except FileNotFoundError:
        return 0


def verify(path: str, expected_size: int, expected_sha: Optional[str]) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty on success."""
    if not Path(path).exists():
        return False, "missing"
    actual_size = file_size(path)
    if expected_size and actual_size != expected_size:
        return False, f"size mismatch ({actual_size} vs {expected_size})"
    if expected_sha:
        actual_sha = file_sha256(path)
        if actual_sha.lower() != expected_sha.lower():
            return False, f"sha256 mismatch ({actual_sha} vs {expected_sha})"
    return True, ""
