"""批 104：`proxy.probe` 的**瞬态重试**。

背景（102b 上产实测）：probe 原为**单发** `requests.get`，经 socks5h 代理偶发
`ConnectionError: Connection reset by peer` 就直接判「出口不可用」⇒ 集成中心「测试出口」
**误报**。反证：**同一代理**上 `okx_perp_daily` 485 标的 × 30 天全窗拉通 ⇒ 出口其实可用。

本批语义：**只重试连接层瞬态**（`ConnectionError`/`Timeout`），按 0.5/1.0s 退避；
HTTP 4xx/5xx 与 `InvalidSchema`（确定性错误，如缺 PySocks）**不重试**。**失败仍不抛**。
"""
import requests
from unittest.mock import MagicMock, patch

from src.data_platform import proxy as P


def _ok_resp(ip: str = "1.2.3.4"):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"ip": ip}
    return r


def _run(side_effect, **kw):
    """跑一次 probe，返回 (结果, get mock, sleep mock)。"""
    row = {"url": "socks5h://127.0.0.1:12345"}
    with patch.object(P, "get_proxy_raw", return_value=row), \
         patch.object(P.requests, "get", side_effect=side_effect) as get, \
         patch.object(P.time, "sleep") as sleep:
        out = P.probe("aws", **kw)
    return out, get, sleep


_RESET = requests.exceptions.ConnectionError(
    ConnectionResetError(104, "Connection reset by peer"))


class TestProbeRetry:
    def test_transient_then_success_retries_once(self):
        """首发被重置、次发成功 ⇒ ok=True、attempts=2，且退避恰一次（0.5s）。"""
        out, get, sleep = _run([_RESET, _ok_resp()])
        assert out["ok"] is True
        assert out["exit_ip"] == "1.2.3.4"
        assert out["attempts"] == 2
        assert get.call_count == 2
        sleep.assert_called_once_with(P.PROBE_RETRY_BACKOFF[0])

    def test_all_transient_returns_false_without_raising(self):
        """恒失败 ⇒ 不抛、attempts=3、逐次错误都在 errors 里，退避序列 0.5/1.0（第 3 次后不再等）。"""
        out, get, sleep = _run([_RESET] * 3)
        assert out["ok"] is False
        assert out["attempts"] == 3
        assert "ConnectionError" in out["error"]
        assert len(out["errors"]) == 3
        assert get.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]

    def test_backoff_clamps_at_last_step(self):
        """退避表只有 2 项 ⇒ 第 3 次失败（若 attempts 调大）仍用最后一项，不越界。"""
        out, get, sleep = _run([_RESET] * 5, attempts=5)
        assert out["attempts"] == 5
        assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0, 1.0, 1.0]

    def test_deterministic_error_not_retried(self):
        """`InvalidSchema`（缺 PySocks 的指纹）是确定性错误 ⇒ **不重试**，一次即返回。"""
        err = requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support.")
        out, get, sleep = _run([err])
        assert out["ok"] is False
        assert out["attempts"] == 1
        assert "InvalidSchema" in out["error"]
        assert get.call_count == 1 and not sleep.called

    def test_http_error_not_retried(self):
        """目标是 5xx ⇒ 连接层是通的，属确定性结果 ⇒ 不重试。"""
        r = MagicMock()
        r.status_code = 503
        out, get, sleep = _run([r])
        assert out["ok"] is False and out["status"] == 503
        assert out["attempts"] == 1
        assert get.call_count == 1 and not sleep.called

    def test_success_first_try_no_sleep(self):
        out, get, sleep = _run([_ok_resp()])
        assert out["ok"] is True and out["attempts"] == 1
        assert get.call_count == 1 and not sleep.called

    def test_attempts_param_override(self):
        """`attempts=1` ⇒ 退回单发语义（老行为可复现，便于对照）。"""
        out, get, _ = _run([_RESET] * 3, attempts=1)
        assert out["attempts"] == 1 and get.call_count == 1

    def test_missing_proxy_short_circuits_before_any_request(self):
        with patch.object(P, "get_proxy_raw", return_value=None), \
             patch.object(P.requests, "get") as get:
            out = P.probe("nope")
        assert out["ok"] is False and "不存在" in out["error"]
        assert get.call_count == 0
