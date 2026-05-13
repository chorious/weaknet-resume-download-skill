from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional


@dataclass
class FileRecord:
    size: int
    sha256: Optional[str] = None
    verified_at: Optional[float] = None
    attempts: int = 0
    last_error: Optional[str] = None


@dataclass
class Manifest:
    repo_id: str = ""
    revision: str = "main"
    files: Dict[str, FileRecord] = field(default_factory=dict)
    updated_at: float = 0.0

    @classmethod
    def load(cls, target_dir: str) -> "Manifest":
        path = Path(target_dir) / ".weaknet-dl" / "manifest.json"
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        m = cls(
            repo_id=raw.get("repo_id", ""),
            revision=raw.get("revision", "main"),
            updated_at=raw.get("updated_at", 0.0),
        )
        for k, v in raw.get("files", {}).items():
            m.files[k] = FileRecord(**v)
        return m

    def save(self, target_dir: str) -> None:
        path = Path(target_dir) / ".weaknet-dl" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "repo_id": self.repo_id,
                    "revision": self.revision,
                    "updated_at": self.updated_at,
                    "files": {k: asdict(v) for k, v in self.files.items()},
                },
                f,
                indent=2,
            )

    def is_verified(self, fname: str, expected_size: int, expected_sha: Optional[str]) -> bool:
        rec = self.files.get(fname)
        if not rec or not rec.verified_at:
            return False
        if expected_size and rec.size != expected_size:
            return False
        if expected_sha and rec.sha256 and rec.sha256 != expected_sha:
            return False
        return True

    def mark_verified(self, fname: str, size: int, sha256: Optional[str]) -> None:
        self.files[fname] = FileRecord(
            size=size, sha256=sha256, verified_at=time.time(), attempts=self.files.get(fname, FileRecord(size=0)).attempts
        )

    def record_attempt(self, fname: str, error: Optional[str] = None) -> None:
        rec = self.files.get(fname) or FileRecord(size=0)
        rec.attempts += 1
        rec.last_error = error
        self.files[fname] = rec
