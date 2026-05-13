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

    # aria2 daemon
    aria2_path: str = "aria2c"
    rpc_port: int = 6800

    # download tuning
    connections: int = 8
    max_retries: int = 20
    stuck_timeout: int = 120  # seconds without progress before abort

    # CAS URL refresh
    refresh_lead_seconds: int = 600     # refresh if URL expires within N seconds
    refresh_interval_seconds: int = 3000  # also refresh on a periodic clock (50 min)

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
            aria2_path=args.aria2_path or os.environ.get("WEAKNET_ARIA2", "aria2c"),
            rpc_port=args.rpc_port,
            connections=args.connections,
            max_retries=args.max_retries,
            stuck_timeout=args.stuck_timeout,
            refresh_lead_seconds=args.refresh_lead,
            verify_sha256=not args.no_verify,
            dry_run=args.dry_run,
        )
