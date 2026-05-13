from __future__ import annotations

from typing import List

import yaml

from . import engine
from .config import Config


def _parse_yaml(path: str) -> List[Config]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError("repos yaml must be a list of {repo, dir, ...} entries")
    out: List[Config] = []
    for entry in raw:
        out.append(Config(
            repo_id=entry["repo"],
            local_dir=entry["dir"],
            proxy=entry.get("proxy"),
            include_regex=entry.get("include"),
            exclude_regex=entry.get("exclude"),
            revision=entry.get("revision", "main"),
            hf_endpoint=entry.get("hf_endpoint", "https://huggingface.co"),
            aria2_proxy=entry.get("aria2_proxy"),
            connections=int(entry.get("connections", 8)),
            max_retries=int(entry.get("max_retries", 20)),
            stuck_timeout=int(entry.get("stuck_timeout", 120)),
            refresh_lead_seconds=int(entry.get("refresh_lead", 600)),
            min_speed_threshold=int(entry.get("min_speed", 50 * 1024)),
            verify_sha256=bool(entry.get("verify", True)),
        ))
    return out


def run_batch(yaml_path: str) -> int:
    configs = _parse_yaml(yaml_path)
    worst = 0
    for cfg in configs:
        rc = engine.run(cfg)
        if rc > worst:
            worst = rc
    return worst
