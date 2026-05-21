"""Unit tests for the ModelScope backend.

These verify the URL construction and file-list parsing without hitting the
network. The MS file-list response fixture mirrors the actual JSON returned by
``GET /api/v1/models/{repo}/repo/files?Recursive=true``.
"""
from unittest.mock import MagicMock, patch

from weaknet_dl import ms_api
from weaknet_dl.hf_api import HFFile


_FIXTURE = {
    "Data": {
        "Files": [
            {"Type": "tree", "Path": "subdir", "Name": "subdir"},
            {"Type": "blob", "Path": ".gitattributes", "Name": ".gitattributes",
             "Size": 1519, "Sha256": "deadbeef", "IsLFS": False},
            {"Type": "blob", "Path": "config.json", "Name": "config.json",
             "Size": 659, "Sha256": "cafe", "IsLFS": False},
            {"Type": "blob", "Path": "model.safetensors", "Name": "model.safetensors",
             "Size": 988097824, "Sha256": "feedface", "IsLFS": True},
        ]
    }
}


def _mock_client(payload):
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get.return_value = mock_resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=None)
    return client


def test_resolve_url_constructs_static_modelscope_url():
    r = ms_api.resolve_url("owner/repo", "path/to/model.bin", revision="master")
    assert r.domain == "modelscope.cn"
    assert r.url.startswith("https://modelscope.cn/api/v1/models/owner/repo/repo?")
    assert "Revision=master" in r.url
    assert "FilePath=path%2Fto%2Fmodel.bin" in r.url


def test_resolve_url_respects_custom_endpoint():
    r = ms_api.resolve_url("owner/repo", "f.bin", endpoint="https://www.modelscope.cn")
    assert r.url.startswith("https://www.modelscope.cn/api/v1/models/owner/repo/repo?")


def test_list_files_filters_trees_and_dotfiles_and_yields_hffiles():
    with patch("weaknet_dl.ms_api._client", return_value=_mock_client(_FIXTURE)):
        files = list(ms_api.list_files("owner/repo"))
    paths = [f.path for f in files]
    # tree entry must be dropped; dotfiles must be skipped
    assert "subdir" not in paths
    assert ".gitattributes" not in paths
    assert "config.json" in paths
    assert "model.safetensors" in paths
    safetensors = next(f for f in files if f.path == "model.safetensors")
    assert isinstance(safetensors, HFFile)
    assert safetensors.size == 988097824
    assert safetensors.sha256 == "feedface"
    assert safetensors.is_lfs is True


def test_list_files_applies_include_regex():
    with patch("weaknet_dl.ms_api._client", return_value=_mock_client(_FIXTURE)):
        files = list(ms_api.list_files("owner/repo", include_regex=r"\.safetensors$"))
    assert [f.path for f in files] == ["model.safetensors"]


def test_list_files_applies_exclude_regex():
    with patch("weaknet_dl.ms_api._client", return_value=_mock_client(_FIXTURE)):
        files = list(ms_api.list_files("owner/repo", exclude_regex=r"\.safetensors$"))
    paths = [f.path for f in files]
    assert "model.safetensors" not in paths
    assert "config.json" in paths
