"""Tests for the aria2 daemon arg builder. No aria2c subprocess needed."""
from weaknet_dl.aria2_daemon import _build_args
from weaknet_dl.config import Config


def _cfg(**over):
    base = dict(repo_id="x/y", local_dir="/tmp/x", connections=4)
    base.update(over)
    return Config(**base)


def test_build_args_no_proxy_by_default():
    args = _build_args(_cfg(), rpc_secret="SECRET")
    assert not any(a.startswith("--all-proxy=") for a in args)
    assert "--rpc-secret=SECRET" in args


def test_build_args_appends_all_proxy_when_set():
    cfg = _cfg(aria2_proxy="socks5://127.0.0.1:10808")
    args = _build_args(cfg, rpc_secret="SECRET")
    assert "--all-proxy=socks5://127.0.0.1:10808" in args


def test_build_args_includes_connections():
    cfg = _cfg(connections=16)
    args = _build_args(cfg, rpc_secret="SECRET")
    assert "--max-connection-per-server=16" in args
    assert "--split=16" in args


def test_build_args_binds_rpc_to_localhost():
    """RPC must not be accessible from LAN — rpc-listen-all=false."""
    args = _build_args(_cfg(), rpc_secret="SECRET")
    assert "--rpc-listen-all=false" in args
