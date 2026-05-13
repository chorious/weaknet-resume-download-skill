from __future__ import annotations

import os
from pathlib import Path

from .base import DownloadResult
from ..config import Config
from ..hf_api import HFFile


class HFHubBackend:
    name = "hf"

    def download_one(self, cfg: Config, f: HFFile, dest_path: str) -> DownloadResult:
        if cfg.proxy:
            os.environ.setdefault("HTTPS_PROXY", cfg.proxy)
            os.environ.setdefault("HTTP_PROXY", cfg.proxy)
            os.environ.setdefault("ALL_PROXY", cfg.proxy)
        if cfg.hf_token:
            os.environ.setdefault("HF_TOKEN", cfg.hf_token)

        try:
            from huggingface_hub import hf_hub_download
        except ImportError as e:
            return DownloadResult(ok=False, error=f"huggingface_hub not installed: {e}")

        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            got_path = hf_hub_download(
                repo_id=cfg.repo_id,
                filename=f.path,
                revision=cfg.revision,
                local_dir=cfg.local_dir,
                token=cfg.hf_token,
                force_download=False,
            )
        except Exception as e:
            return DownloadResult(ok=False, error=f"{type(e).__name__}: {e}")

        size = Path(got_path).stat().st_size if Path(got_path).exists() else 0
        return DownloadResult(ok=True, bytes_written=size)
