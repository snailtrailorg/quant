"""批 66b：回灌脚本键分类单测（prod 干跑实锤 bug 的回归钉——anchor 错位 symbol 解析）。"""
import importlib.util
import os
from unittest.mock import MagicMock

_SPEC = importlib.util.spec_from_file_location(
    "backfill_hub_bars",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "backfill_hub_bars.py"))
bf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bf)


def _fake_r(keys_map):
    """scan_iter/xrange 按 keys_map（key→entries）替身。"""
    r = MagicMock()
    r.scan_iter.return_value = iter(list(keys_map))
    r.xrange.side_effect = lambda k, a, b: keys_map[k]
    return r


def _run(monkeypatch, keys_map, account_id=1, commit=False):
    r = _fake_r(keys_map)
    monkeypatch.setattr(bf, "_redis", lambda: r)
    monkeypatch.setattr("sys.argv", ["x", "--account-id", str(account_id)] + (["--commit"] if commit else []))
    bf.main()


def test_old_key_symbol_parsed_not_literal(monkeypatch, capsys):
    """prod 实锤回归钉：旧键 symbol=parts[2]（非 parts[1] 字面量 'bars'）。"""
    _run(monkeypatch, {"hub:bars:600000.SHSE": [("1-1", {"gen": "203", "ts": "t", "close": "1"})]})
    out = capsys.readouterr().out
    assert "hub:bars:600000.SHSE → hub:bars:1:600000.SHSE" in out
    assert "hub:bars:1:bars" not in out   # 字面量解析形态必须绝迹


def test_new_form_keys_skipped(monkeypatch, capsys):
    """新形态（四段+account 纯数字）跳过——含本账号目标键与跨账号键。"""
    _run(monkeypatch, {"hub:bars:1:600000.SHSE": [("1-1", {})], "hub:bars:9:X.SHSE": [("1-1", {})]})
    out = capsys.readouterr().out
    assert "→" not in out   # 全跳过


def test_commit_writes_with_gen0_and_account(monkeypatch):
    """--commit：XADD 补 account_id+gen 置 0（倒挂防线）。"""
    r = _fake_r({"hub:bars:600000.SHSE": [("1-1", {"gen": "203", "ts": "t", "close": "1"})]})
    monkeypatch.setattr(bf, "_redis", lambda: r)
    monkeypatch.setattr("sys.argv", ["x", "--account-id", "1", "--commit"])
    bf.main()
    call = r.pipeline.return_value.xadd.call_args
    assert call.args[0] == "hub:bars:1:600000.SHSE"
    assert call.args[1]["account_id"] == "1" and call.args[1]["gen"] == "0"
