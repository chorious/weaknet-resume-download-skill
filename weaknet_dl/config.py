from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

LARGE_EXTS = (
    ".safetensors", ".gguf", ".bin", ".pt", ".pth",
    ".onnx", ".ckpt", ".msgpack", ".tflite",
)
SMALL_LIMIT = 50 * 1024 * 1024


@dataclass
class Config:
    repo_id: str
    local_dir: str
    proxy: Optional[str] = None
    hf_token: Optional[str] = None
    backend: str = "hf"
    include_regex: Optional[str] = None
    exclude_regex: Optional[str] = None
    revision: str = "main"
    connections: int = 8
    max_retries: int = 20
    stuck_timeout: int = 120
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
            backend=args.backend or os.environ.get("WEAKNET_BACKEND", "hf"),
            include_regex=args.include,
            exclude_regex=args.exclude,
            revision=args.revision,
            connections=args.connections,
            max_retries=args.max_retries,
            stuck_timeout=args.stuck_timeout,
            verify_sha256=not args.no_verify,
            dry_run=args.dry_run,
        )
