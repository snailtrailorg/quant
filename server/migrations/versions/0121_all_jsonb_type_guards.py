"""0121：全库 json/jsonb 列**类型守卫** CHECK（jsonb 静默降级治理 —— A 层存储防线）。

**承 0120**：0120 只给「config_store 契约面」两列（`data_source.params` / `trading_account.params`）
加了 object 守卫。2026-09-30 全库普查（`information_schema` × `pg_constraint` 交叉）发现：
public 下 json/jsonb 列共 **24** 个，**只有那 2 个有守卫**——其余 22 列零防线；而其中多个列的
写路径与出事前 `data_source` 的形态**逐字同形**（`json.dumps(...)` 交给 `%s::jsonb`），
`security_master.upsert_state` 的 docstring 甚至直接写着入参是 `value_json_str`（已序列化串）。
好消息：普查时这 22 列**没有一行损坏**（全库 `jsonb_typeof='string'` 计数 = 0）——「有风险、无病灶」。

**本迁移补齐的那一层**：类型守卫 CHECK。理由与 0120 同：应用层守卫只看得见「走本函数」的调用方，
CHECK 是**唯一能看见所有写者**的一层（未来新写路径 / 手工 SQL / 运维脚本 / 一次性数据搬运
都绕过应用层）。应用层同批收口于 `quant_common/jsonb.py`（驱动级 `Jsonb` 包装 + `str` 拒收）。

**三条值域律（按列语义分派，不是一刀切）**：

| 律 | 表达式（NULL 一律放行） | 用于 |
|---|---|---|
| object | `jsonb_typeof(col::jsonb) = 'object'` | 语义为对象的列（配置 / 状态 / 指纹 / 归因 / 载荷） |
| array | `jsonb_typeof(col::jsonb) = 'array'` | 语义为数组的列（列表 / 序列 / 候选链） |
| structured | `jsonb_typeof(col::jsonb) <> 'string'` | **形态未明**的列 —— 不猜形状，只钉「必须是结构化值」，精确命中本隐患类（双重编码的产物恰是字符串） |

`::jsonb` 转型对 jsonb 列是无副作用的同型转换、对全库唯一的 `json` 列（`im_bot_config.params`）
是必需的一次解析——统一写法免按列类型分叉。NULL 放行：PG 的 CHECK 对 NULL 表达式本就放行，
且「显式清空」是既有语义之一。

**为什么这两列只钉 `structured` 而不猜 object/array**：`restate_event.payload`（0 行、src 内
无写入点与消费方；表由 0094 建、PIT 载体待实现）与 `alert_channel_sub.categories`（legacy 表，
`alerts.py` 注释「旧表保留不读写」，无写入点）——**猜一个形状会在未来实现者写入合法形态时误伤**。
钉「非字符串」既覆盖本隐患类、又不预判未定的设计。

**加约束前先预检**：逐列断言现存量合规，不合规则 `RuntimeError` 点名（表.列 + 违规行数），
比 PG 的 23514 更好读。**本迁移不改写任何数据**（与 0120 不同：0120 要修存量，0121 无存量可修）。

**downgrade**：只 DROP 本迁移的 22 条约束（0120 的两条不动）。

**不改列集 → `schema_expectations.txt` 无需重生成**（该文件只记 `table :: columns`，本迁移零列变更）。
**不走 allow_contract**：ADD CONSTRAINT = 非破坏性（无列/表/行删除）。

Revision ID: 0121
Revises: 0120
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0121"
down_revision: str = "0120"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表, 列, 律)——律 ∈ {"object","array","structured"}。
# 面 = 全库 json/jsonb 列中 **0120 未覆盖**的全部 22 列（0120 已管 data_source.params /
# trading_account.params，不重复列出——重复 ADD 会因重名失败，也避免「谁管哪列」两处真相）。
_GUARDS = (
    # —— object：语义为对象（读写入点/模型声明逐个确认，非按列名猜）——
    ("im_bot_config", "params", "object"),          # 全库唯一 json 型列：{route_key}
    ("security_state", "value", "object"),          # 时变状态载荷 {conv_price}/{is_st}
    ("security_master", "routing_hints", "object"),  # NOT NULL + DDL 默认 '{}'::jsonb
    ("convertible_terms", "terms", "object"),
    ("notifications", "dispatch", "object"),        # 服务端 jsonb_build_object 构造，仍钉住
    ("routing_decision", "req_summary", "object"),
    ("routing_policy", "weights", "object"),
    ("routing_policy", "bulkhead_defaults", "object"),
    ("strategy_config", "params", "object"),        # 模型 params: dict
    ("strategy_config", "aggregator", "object"),    # 模型 aggregator: dict
    ("strategy_config", "risk", "object"),          # 模型 risk: dict
    ("perm_resource", "label_json", "object"),      # 前端传 {label_zh,label_en}
    ("order_log", "bar_fingerprint", "object"),     # 驱动本单的最近 bar ts+OHLCV
    ("astock_analysis", "factors", "object"),       # dataclass factors: dict
    # —— array：语义为数组 ——
    ("alert_user_sub", "categories", "array"),
    ("alert_user_sub", "channels", "array"),        # 三态 None/[]/非空 → NULL 或 array
    ("market_hours", "sessions", "array"),
    ("market_session", "session_rules", "array"),
    ("routing_decision", "chain", "array"),         # 候选链（adapter 名序列）
    ("strategy_config", "factors", "array"),        # 模型 factors: list
    # —— structured：形态未明（无写入点）——只钉「非字符串」——
    ("alert_channel_sub", "categories", "structured"),
    ("restate_event", "payload", "structured"),
)

# 合规律 / 违规律 的成对表达式（预检用违规式，约束用合规律式）——同一处定义，防两套口径漂移。
_OK = {
    "object": "jsonb_typeof({c}::jsonb) = 'object'",
    "array": "jsonb_typeof({c}::jsonb) = 'array'",
    "structured": "jsonb_typeof({c}::jsonb) <> 'string'",
}
_BAD = {
    "object": "jsonb_typeof({c}::jsonb) <> 'object'",
    "array": "jsonb_typeof({c}::jsonb) <> 'array'",
    "structured": "jsonb_typeof({c}::jsonb) = 'string'",
}


def _ck_name(tbl: str, col: str, law: str) -> str:
    return f"ck_{tbl}_{col}_{law}"


def _expr(col: str, law: str) -> str:
    """CHECK 表达式：NULL 放行 + 该列的合规律。"""
    return f"{col} IS NULL OR {_OK[law].format(c=col)}"


def _bad_rows(conn, tbl: str, col: str, law: str) -> int:
    """违规行数（NULL 不算违规）。"""
    return conn.exec_driver_sql(
        f"SELECT count(*) FROM {tbl} WHERE {col} IS NOT NULL AND "
        + _BAD[law].format(c=col)).scalar()


def upgrade() -> None:
    conn = op.get_bind()
    for tbl, col, law in _GUARDS:
        bad = _bad_rows(conn, tbl, col, law)
        if bad:
            raise RuntimeError(
                f"0121 中止：{tbl}.{col} 有 {bad} 行不符合「{law}」律——本迁移不猜值、"
                "不改写数据；请先人工裁定这些行（判据见本文件头三条值域律）后重跑")
        op.execute(
            f"ALTER TABLE {tbl} ADD CONSTRAINT {_ck_name(tbl, col, law)} "
            f"CHECK ({_expr(col, law)})")
    print(f"[0121] 补齐 {len(_GUARDS)} 条 json/jsonb 类型守卫 CHECK（含 0120 共 "
          f"{len(_GUARDS) + 2} 条 = 全库 json/jsonb 列全覆盖）")


def downgrade() -> None:
    for tbl, col, law in _GUARDS:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {_ck_name(tbl, col, law)}")
