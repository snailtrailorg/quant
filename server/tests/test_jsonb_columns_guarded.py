"""json/jsonb 列**类型守卫枚举闸门**（A 层存储防线的防腐化闸）+ 单出口律（B 层）。

## 为什么需要这条闸门（它才是「以后不会再发生」的等价物）

2026-09-30 全库普查：public 下 json/jsonb 列共 24 个，**只有 2 个有守卫**（迁移 0120 的
`config_store` 契约面）。病根不是「漏了两列」，而是**防线挂在表上、不在类上**——新表/新列
默认裸奔，且没有任何东西会响。本文件把「每列都必须有类型守卫」变成**可执行断言**：
第 25 个 jsonb 列出现时，这条闸门立刻变红，逼作者当场做决定（加守卫或显式豁免并写理由）。

失效模式回顾（静默三重）：`json.dumps(json.dumps(x))` 把对象降级成 jsonb **字符串** →
① 写入端全绿 ② 读端 `json.loads` 兜底自愈 ③ 只有 SQL 里 `col->>'k'` 的消费方悄悄取不到值。
**驱动包装不防**（实测 psycopg 3.3.4：`Jsonb(str)` 同样产出 `jsonb_typeof='string'`）。

## 钉什么（5 段）

1. **枚举全覆盖**（真库）：每个 json/jsonb 列必须有一条类型守卫 CHECK，否则须精确命中
   显式豁免表并写明理由（照 `tests/test_route_auth_gate.py` 的白名单先例 + 反向校验防腐化）。
2. **守卫律只能是三条之一**（object / array / structured）——挡「随便加条废 CHECK 糊过闸门」。
3. **三条律真的会咬**（真库临时表实测）：PG 拒绝双重编码串（23514）、放行合法形态。
   与 (2) 合起来 ⇒ **每个列都是行为级受护的**，不只是「有条 CHECK」。
4. **全库零损坏**：任何 json/jsonb 列都不许出现 `jsonb_typeof='string'`。
5. **单出口律**（无库可跑）：`data_platform.jsonb` 是全仓唯一 jsonb 载荷序列化实现
   （AST 数 `json.dumps` 调用 = 1），且 `jsonb()` 对 str/bytes 响亮拒绝。

**覆盖边界（诚实声明）**：本闸门管的是**存储层**——它看得见所有写者（含手工 SQL/运维脚本），
这是它能成为保证的原因。但它**不管**「代码里是否又出现手工 dumps 的新调用点」：那类回归靠
契约钉（`test_security_upsert.py::test_serialized_string_rejected`、
`test_config_params_contract.py::TestSingleOutletNoDrift`）在**定义契约的那两处**拦。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

# 合规豁免表："table.column" -> 理由（**现状应为空**：24 列全覆盖）。
# 加豁免会让 test_exempt_list_is_empty_today 变红——这是**故意的**：逼豁免决定进评审视野，
# 而不是悄悄往白名单里塞一行。真需要豁免时，连同理由一起改那一条。
_EXEMPT: dict[str, str] = {}

# 三条值域律：表达式（PG 归一闪形态）→ (操作符, 字面量)
_LAWS: dict[str, tuple[str, str]] = {
    "object": ("=", "object"),
    "array": ("=", "array"),
    "structured": ("<>", "string"),
}
_LAW_BY_PAIR = {v: k for k, v in _LAWS.items()}

# 提取 `jsonb_typeof(<col>[::jsonb]) <op> '<lit>'`（PG 会把 `col::jsonb` 归一成
# `(col)::jsonb`、把字面量加 `::text` 后缀，故用宽松匹配 + 回验列名）
_GUARD_RE = re.compile(
    r"jsonb_typeof\(\s*\(?\s*(\w+)\s*\)?\s*(?:::jsonb)?\s*\)\s*(=|<>)\s*'(object|array|string)'")


def _db_up() -> bool:
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _json_columns(conn) -> list[tuple[str, str, str]]:
    """库内 public 下全部 (表, 列, 类型)。"""
    return conn.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND data_type IN ('json','jsonb') "
        "ORDER BY table_name, column_name").fetchall()


def _guards_of(conn, tbl: str) -> list[tuple[str, str, str, str]]:
    """该表上带 `jsonb_typeof` 的 CHECK：(约束名, 列名, 操作符, 字面量)。"""
    out = []
    for name, definition in conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND contype='c' "
            "AND pg_get_constraintdef(oid) LIKE %s ORDER BY conname",
            (tbl, "%jsonb_typeof%")).fetchall():
        for col, op, lit in _GUARD_RE.findall(definition):
            out.append((name, col, op, lit))
    return out


def _calls_in(path: Path, fn: str | None = None) -> list[str]:
    """`fn` 函数体内被调用的点分名列表（AST——文本扫描会被 docstring 自伤）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    nodes = [n for n in scopes if n.name == fn] if fn else [tree]
    out = []
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name):
                    out.append(f.id)
                elif isinstance(f, ast.Attribute):
                    parts, cur = [], f
                    while isinstance(cur, ast.Attribute):
                        parts.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        parts.append(cur.id)
                    out.append(".".join(reversed(parts)))
    return out


# ───────────────────────── 一、枚举全覆盖（真库） ─────────────────────────


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestGuardCoverage:
    def test_every_json_column_has_type_guard(self):
        """**本闸门的核心断言**：每个 json/jsonb 列要么有类型守卫 CHECK，要么在豁免表里。"""
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            unguarded = []
            for tbl, col, _dt in _json_columns(conn):
                guarded = any(c == col for _n, c, _o, _l in _guards_of(conn, tbl))
                if not guarded and f"{tbl}.{col}" not in _EXEMPT:
                    unguarded.append(f"{tbl}.{col}")
        assert not unguarded, (
            f"这些 json/jsonb 列没有类型守卫 CHECK：{unguarded}\n"
            "→ 加约束（迁移，三条律见 tests 文档头）或显式豁免并写理由。"
            "jsonb 静默降级（json.dumps 双重编码）会在这层被 23514 拦下，别摘掉它")

    def test_guard_law_is_one_of_three(self):
        """守卫律只能取 object / array / structured 三值——防止加条空 CHECK 糊过闸门。"""
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            bad = []
            for tbl, col, _dt in _json_columns(conn):
                for name, c, op, lit in _guards_of(conn, tbl):
                    if c == col and (op, lit) not in _LAW_BY_PAIR:
                        bad.append(f"{tbl}.{col} <- {name}: {op} '{lit}'")
        assert not bad, f"守卫表达了三条律之外的形态（可能是糊闸门的废约束）：{bad}"

    def test_no_column_currently_corrupted(self):
        """全库零损坏：任何 json/jsonb 列都不许有 `jsonb_typeof='string'` 的行。"""
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            broken = {}
            for tbl, col, _dt in _json_columns(conn):
                n = conn.execute(
                    f'SELECT count(*) FROM "{tbl}" WHERE jsonb_typeof("{col}"::jsonb) = \'string\''
                ).fetchone()[0]
                if n:
                    broken[f"{tbl}.{col}"] = n
        assert not broken, (
            f"发现 jsonb 字符串行（静默降级已发生）：{broken}\n"
            "→ 修法见迁移 0120 的 `col #>> '{}'` 解一层引用；修完再查根因（哪个写者绕过了守卫）")

    def test_exempt_list_is_empty_today(self):
        """豁免表现状应为空（24 列全覆盖）。**要加豁免请连同理由一起改本条**。"""
        assert _EXEMPT == {}, (
            f"豁免表非空：{list(_EXEMPT)}\n"
            "→ 这不是 bug，但必须是有意识决定：确认该列无法定形状（无写入点/legacy 冻结）后，"
            "把理由写进 _EXEMPT 并更新本条断言")

    def test_exempt_entries_are_live_and_reasoned(self):
        """豁免表反向校验（防腐化）：每条必须指向**真实存在**的列、**确实无守卫**、且写了理由。"""
        if not _EXEMPT:
            pytest.skip("豁免表为空——反向校验无事可做")
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            existing = {f"{t}.{c}" for t, c, _ in _json_columns(conn)}
            for key, reason in _EXEMPT.items():
                assert key in existing, f"豁免表里有不存在的列：{key}（陈腐条目应删）"
                assert reason.strip(), f"豁免 {key} 没写理由"
                tbl, col = key.split(".", 1)
                guarded = any(c == col for _n, c, _o, _l in _guards_of(conn, tbl))
                assert not guarded, f"{key} 已有守卫，豁免条目应删（否则豁免表会越积越肥）"


# ───────────────────────── 二、三条律真的会咬（真库） ─────────────────────────

# 律 -> (非法字面量, 合法字面量)。非法值一律是「双重编码产物」（JSON 字符串）
_LAW_CASES = {
    "object": ('"{\\"a\\": 1}"', '{"a": 1}'),
    "array": ('"[1,2]"', "[1,2]"),
    "structured": ('"{\\"a\\": 1}"', "[1,2]"),
}


@pytest.mark.skipif(not _db_up(), reason="真库行为级（无 dev 库自动跳过）")
class TestLawsActuallyBite:
    """律不只是「有条 CHECK」——实测 PG 会拒非法形态、放行合法形态。"""

    @pytest.mark.parametrize("law", sorted(_LAWS))
    def test_law_rejects_double_encoded_and_accepts_legal(self, law):
        from psycopg.errors import CheckViolation

        from src.data_platform.db import get_conn
        expr = f"jsonb_typeof(v::jsonb) {'=' if _LAWS[law][0] == '=' else '<>'} '{_LAWS[law][1]}'"
        bad, good = _LAW_CASES[law]
        with get_conn() as conn:
            conn.execute("DROP TABLE IF EXISTS _law_probe")
            conn.execute(f"CREATE TEMP TABLE _law_probe (v jsonb CHECK (v IS NULL OR {expr}))")
            try:
                with pytest.raises(CheckViolation) as ei:
                    conn.execute("INSERT INTO _law_probe (v) VALUES (%s)", (bad,))
                assert ei.value.sqlstate == "23514"
                conn.rollback()
                conn.execute("DROP TABLE IF EXISTS _law_probe")
                conn.execute(f"CREATE TEMP TABLE _law_probe (v jsonb CHECK (v IS NULL OR {expr}))")
                conn.execute("INSERT INTO _law_probe (v) VALUES (%s)", (good,))   # 合法形态放行
                conn.execute("INSERT INTO _law_probe (v) VALUES (NULL)")          # NULL 放行
            finally:
                conn.execute("DROP TABLE IF EXISTS _law_probe")
                conn.rollback()

    def test_json_column_variant_needs_cast(self):
        """`json` 型列（全库唯一：`im_bot_config.params`）——守卫必须显式 `::jsonb` 转型
        （实测建得成、且同样拦得住双重编码串）。"""
        from psycopg.errors import CheckViolation

        from src.data_platform.db import get_conn
        with get_conn() as conn:
            conn.execute("DROP TABLE IF EXISTS _json_probe")
            conn.execute("CREATE TEMP TABLE _json_probe (v json CHECK "
                         "(v IS NULL OR jsonb_typeof((v)::jsonb) = 'object'))")
            try:
                with pytest.raises(CheckViolation):
                    conn.execute("INSERT INTO _json_probe (v) VALUES (%s)", ('"{\\"a\\": 1}"',))
                conn.rollback()
            finally:
                conn.execute("DROP TABLE IF EXISTS _json_probe")
                conn.rollback()


# ───────────────────────── 三、单出口律（无库可跑） ─────────────────────────


class TestSingleOutlet:
    def test_outlet_module_has_the_only_dumps(self):
        """`data_platform/jsonb.py` 内 `json.dumps` 调用**恰一处**——全仓 jsonb 载荷序列化的单点。"""
        hits = [c for c in _calls_in(_SRC / "data_platform" / "jsonb.py") if c == "json.dumps"]
        assert len(hits) == 1, f"jsonb 单出口内 json.dumps 调用数 = {len(hits)}（应恰 1）"

    @pytest.mark.parametrize("value", ['{"a": 1}', "null", "[1,2]", "", b"{}", bytearray(b"{}")])
    def test_jsonb_rejects_serialized_shapes(self, value):
        """**契约看类型不看内容**：str/bytes 一律拒（连能过 json.loads 的 `"null"` 串也拒）。"""
        from src.data_platform.jsonb import jsonb
        with pytest.raises(TypeError):
            jsonb(value)

    def test_jsonb_wraps_objects_and_lists(self):
        from psycopg.types.json import Jsonb

        from src.data_platform.jsonb import jsonb
        assert jsonb({"a": 1}).obj == {"a": 1}
        assert jsonb([1, 2]).obj == [1, 2]
        assert isinstance(jsonb({}), Jsonb)          # 空对象仍是对象（不是 None）
        assert jsonb(None) is None                   # None → SQL NULL，非 jsonb null

    def test_dumps_jsonb_keeps_cjk_and_shape(self):
        """文本出口（`%s::jsonb` 惯用法）保中文不转义、空对象→`{}`、None→NULL。"""
        from src.data_platform.jsonb import dumps_jsonb
        assert dumps_jsonb({"备注": "限速"}) == '{"备注": "限速"}'
        assert json.loads(dumps_jsonb({"a": [1, 2]})) == {"a": [1, 2]}
        assert dumps_jsonb({}) == "{}"
        assert dumps_jsonb(None) is None
        with pytest.raises(TypeError):
            dumps_jsonb('{"a": 1}')

    def test_driver_wrapper_alone_would_not_protect(self):
        """**反证本守卫不是洁癖**：驱动级 `Jsonb(str)` 自己也会双重编码（psycopg 3.3.4 实测）
        ——所以「收归驱动」必须带类型守卫，单靠包装不够。"""
        from src.data_platform.jsonb import _dumps
        assert _dumps(_dumps({"a": 1})) == '"{\\"a\\": 1}"'      # 双重编码的产物形态
        assert json.loads(json.loads(_dumps(_dumps({"a": 1})))) == {"a": 1}   # 值没变，形状变了
