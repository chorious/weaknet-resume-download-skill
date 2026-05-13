from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from ..config import Config
from ..hf_api import HFFile


@dataclass
class DownloadResult:
    ok: bool
    bytes_written: int = 0
    error: Optional[str] = None


class Backend(Protocol):
    name: str

    def download_one(self, cfg: Config, f: HFFile, dest_path: str) -> DownloadResult: ...
