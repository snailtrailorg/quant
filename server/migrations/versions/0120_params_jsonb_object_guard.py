"""0120：params 立方——两域表 jsonb **对象守卫**（CHECK）+ 存量双重编码修复。

**病根**：`params` 是 jsonb 列。写路径若把「已序列化的 JSON 字符串」再 dumps 一次
（`json.dumps(json.dumps(x))`），PG 的 `%s::jsonb` 转型**不报错**——它把对象静默降级存成
jsonb **字符串**（库里 `"{\\"a\\": 1}"`，而非 `{"a": 1}`）。这是**三重静默**：
① 写入端全绿（无异常）；② 读侧 `config_store.load_provider_params` 的 `json.loads` 兜底
又把它自愈回 dict（于是看日志、看接口都正常）；③ 只有**在 SQL 里直接读**的消费方
（`params->>'k'`）才取不到值，而那里通常不报错、只返回 NULL——所以能长期潜伏。

**实测存量**（dev 库 2026-09-30；全库 `jsonb` 且列名含 param 的列共 3 个，逐个全扫）：
| 表.列                       | 字符串行 | 总行 | 备注                    |
|-----------------------------|---------|------|-------------------------|
| `data_source.params`        | 1       | 2    | id=75 name='PC-guard'   |
| `trading_account.params`    | 1       | 3    | id=51 name='PC-guard'   |
| `strategy_config.params`    | 0       | 5    | 干净（未损坏）           |
→ 修完这两张表 = 全库该类损坏清零。

**为什么必须加 DB 约束，而不是只靠应用层守卫**（这是本迁移存在的理由）：
`config_store._param_jsonb`（2026-09-30 已落地）只能管**一个模块的调用方**——未来新写
路径、手工 SQL、运维脚本、一次性数据搬运（如 0116 的拆表搬运）都绕过它。CHECK 约束是
**唯一能看见「所有写者」的一层**：它把「静默降级」变成 SQLSTATE **23514**，
报错点紧贴病根，且新写者**一写就撞**、无需它知道有这条律。

**值域**：`params IS NULL OR jsonb_typeof(params) = 'object'`。object-only **不是新律**：
HTTP 边界 `mgmt._normalize_params` 早已是同一律（「合法 JSON 但非对象 → 400
IFACE_PARAMS_INVALID params 须为 JSON 对象」），本约束只是把同一条律往下沉一层——
两条防线表达同一个不变量，不是两套口径。NULL 放行：显式清空 params 是既有三段语义之一
（`_param_jsonb(None)` 返 SQL NULL），且 PG 的 CHECK 对 NULL 表达式本就放行。

**顺序**：先修数据、再加约束（存量不修则 `ADD CONSTRAINT` 必失败）。

**不可自动判定的行 = 响亮中止**：若某行内层文本不是「合法 JSON **对象**」
（内层是数组/标量/坏 JSON），本迁移**不猜值**，`RuntimeError` 中止并要求人工处理——
猜一个值写回比报错危险（可能把有意义的配置改坏且无人知晓）。中止信息给出表名/id/内层原文。

**downgrade**：只 DROP 约束。**修复不可逆**——回滚的语义是「撤掉守卫」，
不是「把静默损坏装回去」（那需要人工重造损坏，本迁移不代劳）。

**不走 allow_contract**：ADD CONSTRAINT + 行内值修复 = 非破坏性（无列/表/行删除）。
**不改列集 → `schema_expectations.txt` 无需重生成**（该文件只记 table :: columns）。

Revision ID: 0120
Revises: 0119
"""
import json
from typing import Sequence, Union

from alembic import op

revision: str = "0120"
down_revision: str = "0119"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# config_store 立法的两域表（域=表）——本迁移的守卫面精确等于「config_store 的 params 契约面」
_TABLES = ("data_source", "trading_account")


def _constraint_name(tbl: str) -> str:
    return f"ck_{tbl}_params_object"


def _repair_string_rows(conn, tbl: str) -> int:
    """修复「jsonb 字符串」行：解一层引用还原内层 JSON 对象。

    `params #>> '{}'` = 取出 jsonb 字符串标量的**内层文本**（`"{\\"a\\": 1}"` → `{"a": 1}`），
    再 `json.loads` 判定它是**对象**（而非数组/标量/坏 JSON）。只对判定为对象的行写回。
    返回修复行数（迁移日志可见，便于审阅确认存量规模与预期一致）。
    """
    sql = ("SELECT id, params #>> '{}' FROM " + tbl + " WHERE jsonb_typeof(params) = 'string'")
    fixed = 0
    for rid, inner in conn.exec_driver_sql(sql).fetchall():
        try:
            v = json.loads(inner)
        except ValueError:
            v = None
        if not isinstance(v, dict):
            raise RuntimeError(
                f"0120 中止：{tbl} id={rid} 的 params 是 jsonb 字符串，但内层不是 JSON 对象"
                f"（内层原文 = {inner!r}）——无法自动判定原意，请人工确认该行后重跑本迁移")
        conn.exec_driver_sql(
            f"UPDATE {tbl} SET params=%s::jsonb WHERE id=%s",
            (json.dumps(v, ensure_ascii=False), rid))
        fixed += 1
    return fixed


def _assert_only_objects(conn, tbl: str) -> None:
    """加约束前的预检：给出**比 PG 约束报错更好读**的失败信息（点名违规行）。"""
    bad = conn.exec_driver_sql(
        "SELECT id, jsonb_typeof(params) FROM " + tbl +
        " WHERE params IS NOT NULL AND jsonb_typeof(params) <> 'object'").fetchall()
    if bad:
        raise RuntimeError(
            f"0120 中止：{tbl} 仍有非对象的 params 行 {bad}（id, jsonb_typeof）"
            "——本迁移只修「内层为 JSON 对象」的字符串行；其余形态须人工裁定后再重跑")


def upgrade() -> None:
    conn = op.get_bind()
    for tbl in _TABLES:
        fixed = _repair_string_rows(conn, tbl)
        print(f"[0120] {tbl}: 修复 jsonb 字符串行 {fixed} 行")
        _assert_only_objects(conn, tbl)
        op.execute(
            f"ALTER TABLE {tbl} ADD CONSTRAINT {_constraint_name(tbl)} "
            "CHECK (params IS NULL OR jsonb_typeof(params) = 'object')")


def downgrade() -> None:
    for tbl in _TABLES:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {_constraint_name(tbl)}")
