"""`params` 入参契约统一钉（2026-09-30 顺手裁定）。

**背景**：复审清单 §七「🟡 次要」顺带发现——同一模块（`config_store`）内 params 写路径
**契约不对称**：`insert_row`/`update_row` 收「已序列化的 JSON 字符串」（序列化下放
`mgmt.py`），而 `save_provider_params` 收 dict（内部 `json.dumps`）。两者各自自洽、当时
调用方也都对，但同一模块两种相反契约 = 脆弱契约温床：调用方必须记住「这个函数要不要
dumps」，而**记错的失败模式是静默的**（见下）。

**裁定：统一 → dict 入参 + 内部 dumps（`_param_jsonb` 单出口）**。三条理由：
1. **读写对称**——读路径 `load_provider_params` 已明写「params=jsonb（psycopg 读侧已 dict）」
   且专门 `isinstance(r[1], dict)` 分支；写路径收 dict 后 `config_store` 的 params 契约
   **进出同型**，值可盲传、不携带序列化形态进城。
2. **删掉白跑一趟**——路由层 `_normalize_params` 刚把 HTTP 来的 str/dict 归一成 dict，
   紧接着又 `json.dumps` 变回 str 交给 config_store；统一后这一段消失。
3. **知识归属**——`%s::jsonb` 转型占位符（`_ph`/`_val_ph`）已在本模块，「params 是 jsonb」
   本模块已知，序列化属存储细节，不该外泄给调用方。

**本文件钉什么**（4 段）：
- 单出口语义：`_param_jsonb` 的 dict/None/空 dict/中文保真 + **str 响亮拒绝**；
- **双重编码反证**：为什么必须拒绝 str（`json.dumps(json.dumps(x))` 不报错、静默把对象
  存成 jsonb **字符串**，读写往返才炸且报错点离病根很远）——守卫不是洁癖，是防静默损坏；
- 单出口防漂移：模块内 `json.dumps` 恰一处 + 路由层不再 dumps（源码扫描，防回退）；
- **真库往返钉**：dict 入参落库后 `jsonb_typeof(params)='object'`（对象而非字符串）、读回
  是 dict、`update_row` 同构；**并反证守卫在真库路径同样生效**（传 str 抛 TypeError）。

参照 `tests/test_config_plane.TestRealDbRoundTrip` 的真库钉惯例（`_db_up()` + `skipif`）。
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data_platform import config_store as cs

_SRC = Path(__file__).resolve().parents[1] / "src"


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _calls_in(path: Path, fn_name: str | None = None) -> list[str]:
    """收集调用名（`json.dumps` / `_param_jsonb` …）——**按 AST 而非文本**。

    文本扫描会命中 docstring/注释里提到的函数名（本文件上一版就栽在这：docstring 里解释
    「原在此 json.dumps」把自己扫成了新出口）。AST 只认真实调用，注释爱怎么写都行。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name] \
        if fn_name else [tree]
    if not scopes:
        raise AssertionError(f"{path.name} 内找不到函数 {fn_name}")
    stack, out = list(scopes), []
    while stack:
        for sub in ast.walk(stack.pop()):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if isinstance(f, ast.Attribute):
                out.append(f"{getattr(f.value, 'id', '')}.{f.attr}")
            elif isinstance(f, ast.Name):
                out.append(f.id)
    return out


# ───────────────────────── 一、单出口语义 ─────────────────────────


class TestParamJsonbOutlet:
    """`_param_jsonb`：dict 进、JSON 字符串出；None 进、SQL NULL 出；str 进、响亮拒绝。"""

    def test_dict_dumps_to_json_string(self):
        assert cs._param_jsonb({"a": 1, "b": [1, 2]}) == '{"a": 1, "b": [1, 2]}'

    def test_non_ascii_preserved(self):
        """ensure_ascii=False——中文值不转 \\uXXXX（与前端/人工查看口径一致）。"""
        assert cs._param_jsonb({"备注": "限速"}) == '{"备注": "限速"}'

    def test_empty_dict_is_json_empty_object(self):
        """`{}` → `"{}"`（jsonb 空对象），**不是** SQL NULL——`_normalize_params` 对空入参返 {}。"""
        assert cs._param_jsonb({}) == "{}"

    def test_none_is_sql_null(self):
        """None → SQL NULL（显式清空 params），与「键缺席=不改」的三段语义互补。"""
        assert cs._param_jsonb(None) is None

    def test_serialized_string_loudly_rejected(self):
        with pytest.raises(TypeError) as ei:
            cs._param_jsonb('{"a": 1}')
        assert "双重编码" in str(ei.value)
        assert "dict" in str(ei.value)

    def test_even_json_null_string_rejected(self):
        """连 `"null"` 这种能过 json.loads 的字符串也拒——契约不看内容看**类型**。"""
        with pytest.raises(TypeError):
            cs._param_jsonb("null")

    def test_double_encoding_is_the_hazard_it_guards(self):
        """**反证守卫的必要性**：双重编码不报错，静默把对象降级成字符串。

        若没有守卫，调用方把已序列化串再喂进来会得到 `json.dumps('{"a": 1}')`
        ——它**合法**（PG 照收不误），只是语义从 jsonb 对象变成 jsonb **字符串**：
        下游 `params->>'a'` 取不到、`capabilities`/`rate_limits` 消费方读出血崩，
        而写入端全绿。故此处断言「罪证」存在，守卫是唯一的拦截点。
        """
        good = json.dumps({"a": 1}, ensure_ascii=False)                 # '{"a": 1}'
        double = json.dumps(good, ensure_ascii=False)                  # '"{\\"a\\": 1}"'
        # 双重编码的产物仍能一次 loads 回来，但**类型已从 dict 降级为 str**：
        assert json.loads(double) == good
        assert isinstance(json.loads(double), str)                     # ← 静默损坏，无异常
        assert not isinstance(json.loads(double), dict)
        # 有守卫则在入口就断（不进 PG）：
        with pytest.raises(TypeError):
            cs._param_jsonb(good)


# ───────────────────────── 二、单出口防漂移（源码扫描） ─────────────────────────


class TestSingleOutletNoDrift:
    """防回退：三函数共用一处 dumps；路由层不再承担序列化。"""

    def test_json_dumps_appears_exactly_once_in_module(self):
        """`config_store` 内 `json.dumps` **恰一处**（`_param_jsonb`）——多一处即新出口=漂移。"""
        hits = [c for c in _calls_in(_SRC / "data_platform" / "config_store.py")
                if c == "json.dumps"]
        assert len(hits) == 1, f"json.dumps 出口数 = {len(hits)}（应恰 1 处，见 _param_jsonb）"

    @pytest.mark.parametrize("fn", ["insert_row", "update_row", "save_provider_params"])
    def test_every_writer_routes_through_outlet(self, fn):
        calls = _calls_in(_SRC / "data_platform" / "config_store.py", fn)
        assert {"_param_jsonb", "_row_values"} & set(calls), f"{fn} 未经 params 单出口"

    def test_mgmt_layer_no_longer_dumps(self):
        """路由层 `_write_values` 不再 `json.dumps`（那一段 parse→dump 白跑一趟已删）。"""
        calls = _calls_in(_SRC / "web_api" / "routes" / "mgmt.py", "_write_values")
        assert "json.dumps" not in calls
        assert "_normalize_params" in calls

    def test_write_values_hands_over_a_dict(self):
        """`_write_values` 交出的 params 是 **dict**（契约统一的交接面证据）。"""
        from src.web_api.routes import mgmt
        req = SimpleNamespace(name="T", provider="tushare", market="astock",
                              params='{"rate_limits": {"a": 1}}',   # HTTP 边界仍可传 str
                              capabilities=["hist_quote"], enabled=True, credentials=None)
        values = mgmt._write_values(cs.KIND_DATA, req, ["hist_quote"])
        assert values["params"] == {"rate_limits": {"a": 1}}
        assert isinstance(values["params"], dict)


# ───────────────────────── 三、真库往返钉 ─────────────────────────


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestRealDbParamsContract:
    """真库钉：契约统一的**结果**在库里成立（对象而非字符串），且守卫在真库路径生效。

    为什么必须真库：`_param_jsonb` 的单元断言只能证明「我们送了个字符串进去」，
    证明不了「PG 把它存成了 jsonb 对象」——而「对象还是字符串」正是本契约的成败判据
    （`jsonb_typeof`），假连接永远看不到。
    """

    @staticmethod
    def _mk_values(kind, tag):
        vals = {"name": f"PC-{tag}",
                "provider": "tushare" if kind == cs.KIND_DATA else "xtp",
                "market": "astock", "params": {"rate_limits": {"per_min": 60}},
                "capabilities": ["hist_quote"] if kind == cs.KIND_DATA else ["trading"],
                "enabled": True}
        if kind == cs.KIND_TRADING:
            vals["exchanges"] = ["SHSE"]
            vals["account_key"] = f"PC-{tag}"        # UNIQUE(provider, account_key)
        return vals

    @staticmethod
    def _jsonb_typeof(kind, rid):
        with cs.get_conn() as conn:
            return conn.execute(
                f"SELECT jsonb_typeof(params) FROM {cs._tbl(kind)} WHERE id=%s",
                (rid,)).fetchone()[0]

    @staticmethod
    def _params_of(kind, rid):
        return next(cs.row_dict(r, kind) for r in cs.list_rows(kind) if r[0] == rid)["params"]

    @pytest.mark.parametrize("kind", ["data_source", "trading_account"])
    def test_dict_in_lands_as_jsonb_object(self, kind):
        rid = None
        tag = uuid.uuid4().hex[:8]
        try:
            rid = cs.insert_row(kind, self._mk_values(kind, tag))
            # ① 库内类型是 object（双重编码会存成 'string'——这是本契约的核心判据）
            assert self._jsonb_typeof(kind, rid) == "object"
            # ② 读侧往返成 dict（load_provider_params 的 isinstance(dict) 分支前提）
            assert self._params_of(kind, rid) == {"rate_limits": {"per_min": 60}}
            # ③ UPDATE 同构（SET 子句走 `_ph`，与 VALUES 的 `_val_ph` 是两条独立 SQL）
            cs.update_row(kind, rid, {"name": f"PC2-{tag}", "params": {"b": 2}})
            assert self._jsonb_typeof(kind, rid) == "object"
            assert self._params_of(kind, rid) == {"b": 2}
        finally:
            if rid is not None and cs.row_exists(kind, rid):
                cs.delete_row(kind, rid)

    @pytest.mark.parametrize("kind", ["data_source", "trading_account"])
    def test_string_in_rejected_on_both_write_paths(self, kind):
        """守卫在真库路径同样生效：传字符串在**进 PG 前**就断（不留半事务、不静默降级）。"""
        with pytest.raises(TypeError):
            cs.insert_row(kind, {**self._mk_values(kind, "guard"), "params": '{"a": 1}'})
        rid = None
        try:
            rid = cs.insert_row(kind, self._mk_values(kind, f"guard2"))
            with pytest.raises(TypeError):
                cs.update_row(kind, rid, {"params": '{"a": 1}'})
            # 失败的 update 未污染行（rollback 语义）
            assert self._params_of(kind, rid) == {"rate_limits": {"per_min": 60}}
        finally:
            if rid is not None and cs.row_exists(kind, rid):
                cs.delete_row(kind, rid)

    def test_save_provider_params_same_outlet(self):
        """`save_provider_params` 走同一出口：dict 进 → 库内 object → `load_provider_params` 读回。

        真库限制：本函数只吃**数据源行 id**（`_tbl(KIND_DATA)` 钉死），故此处只测数据域。
        """
        rid = None
        tag = uuid.uuid4().hex[:8]
        try:
            rid = cs.insert_row(cs.KIND_DATA, self._mk_values(cs.KIND_DATA, tag))
            cs.save_provider_params(rid, {"rate_limits": {"per_min": 60}, "cb": {"errors": 5}})
            assert self._jsonb_typeof(cs.KIND_DATA, rid) == "object"
            assert self._params_of(cs.KIND_DATA, rid) == {"rate_limits": {"per_min": 60},
                                                         "cb": {"errors": 5}}
            with pytest.raises(TypeError):
                cs.save_provider_params(rid, '{"a": 1}')
        finally:
            if rid is not None and cs.row_exists(cs.KIND_DATA, rid):
                cs.delete_row(cs.KIND_DATA, rid)

    def test_none_clears_to_sql_null(self):
        """None 走 SQL NULL（**不是** jsonb `null`）——jsonb_typeof 返 NULL 可区分两者。"""
        rid = None
        tag = uuid.uuid4().hex[:8]
        try:
            rid = cs.insert_row(cs.KIND_DATA, self._mk_values(cs.KIND_DATA, tag))
            cs.update_row(cs.KIND_DATA, rid, {"params": None})
            assert self._jsonb_typeof(cs.KIND_DATA, rid) is None
        finally:
            if rid is not None and cs.row_exists(cs.KIND_DATA, rid):
                cs.delete_row(cs.KIND_DATA, rid)
