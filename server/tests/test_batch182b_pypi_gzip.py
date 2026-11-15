"""批182b 守门：PyPI simple 必须走 gzip（大响应慢速传输是 dep_audit 慢的根因）。"""
import gzip
import json
from unittest.mock import MagicMock


def test_pypi_versions_requests_and_decodes_gzip(monkeypatch):
    from src.health_monitor import os_pkg
    payload = {"files": [
        {"filename": "pyzmq-25.1.0-cp311-cp311-manylinux.whl", "yanked": False}]}
    gz = gzip.compress(json.dumps(payload).encode())
    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["headers"] = req.headers
        r = MagicMock()
        r.read.return_value = gz
        r.headers = {"Content-Encoding": "gzip"}
        r.__enter__.return_value = r
        return r

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = os_pkg.pypi_versions("pyzmq")
    assert "gzip" in str(captured["headers"]).lower()
    assert out["versions"] == ["25.1.0"]
