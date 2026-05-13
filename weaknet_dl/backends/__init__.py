from .base import Backend, DownloadResult
from .hf_hub import HFHubBackend
from .aria2 import Aria2Backend


def get_backend(name: str) -> Backend:
    if name == "hf":
        return HFHubBackend()
    if name == "aria2":
        return Aria2Backend()
    raise ValueError(f"unknown backend: {name!r}; expected 'hf' or 'aria2'")
