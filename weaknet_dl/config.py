from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

LARGE_EXTS = (
    ".safetensors", ".gguf", ".bin", ".pt", ".pth",
    ".onnx", ".ckpt", ".msgpack", ".tflite",
)


@dataclass
class Config:
    repo_id: str
    local_dir: str
    proxy: Optional[str] = None
    hf_token: Optional[str] = None
    include_regex: Optional[str] = None
    exclude_regex: Optional[str] = None
    revision: str = "main"

    # HF endpoint (defaults to upstream; set to https://hf-mirror.com to bypass CDN limits)
    hf_endpoint: str = "https://huggingface.co"

    # aria2 daemon
    aria2_path: str = "aria2c"
    rpc_port: int = 6800
    aria2_proxy: Optional[str] = None  # route aria2c bytes through a proxy

    # download tuning
    connections: int = 8
    max_retries: int = 20
    stuck_timeout: int = 120  # seconds without progress before abort

    # CAS URL refresh
    refresh_lead_seconds: int = 600     # refresh if URL expires within N seconds
    refresh_interval_seconds: int = 3000  # also refresh on a periodic clock (50 min)

    # Rate-limit detection / observability
    min_speed_threshold: int = 50 * 1024            # bytes/s; below this triggers rate-limit handler
    rate_limit_cooldown_seconds: int = 300          # don't re-trigger handler within this window
    status_log_interval: int = 30                   # seconds between periodic progress lines

    # verification
    verify_sha256: bool = True
    dry_run: bool = False

    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env_and_args(cls, args) -> "Config":
        return cls(
            repo_id=args.repo_id,
            local_dir=args.local_dir,
            proxy=args.proxy or os.environ.get("WEAKNET_PROXY"),
            hf_token=os.environ.get("HF_TOKEN"),
            include_regex=args.include,
            exclude_regex=args.exclude,
            revision=args.revision,
            hf_endpoint=args.hf_endpoint or os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
            aria2_path=args.aria2_path or os.environ.get("WEAKNET_ARIA2", "aria2c"),
            rpc_port=args.rpc_port,
            aria2_proxy=args.aria2_proxy or os.environ.get("WEAKNET_ARIA2_PROXY"),
            connections=args.connections,
            max_retries=args.max_retries,
            stuck_timeout=args.stuck_timeout,
            refresh_lead_seconds=args.refresh_lead,
            min_speed_threshold=args.min_speed,
            verify_sha256=not args.no_verify,
            dry_run=args.dry_run,
        )
