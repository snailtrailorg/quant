"""批 83a：配置面（数据源/交易账号两族）数据访问——SQL 自路由层下沉（层 1）。

立法（2026-09-29 用户裁定·抽象判据）：行的边界=账号，**域=表**。
  `data_source`      = 数据源域（拉取侧：token/rate_limits/熔断；**无** exchanges/account_key）
  `trading_account`  = 交易账号域（下单/行情侧：exchanges/account_key/连接参数/交易所覆盖）

两族的 CRUD、拖拽重排、限流参数读写、account_permission 存在性判据与读写全在本模块；
路由层（`web_api/routes/mgmt.py`，层 4）只做参数校验/错误码映射/审计——故该文件 SQL 计数
降为 0（守门 `tests/test_no_sql_in_new_routes.py` 基线同步下调；CLAUDE.md 分层铁律：
SQL 下沉 `data_platform` 等层 1-2）。

**跨表禁按 id 推断**：两表 id 各自独立序列（拆表后同值可能同时存在于两表），
任何「这行是不是...」的判断必须带 kind/表名，禁 `WHERE account_id=%s` 式跨表猜。
"""
from __future__ import annotations

import json
import logging

from src.data_platform.db import get_conn
from src.quant_common.markets import DOMAIN_DATA, DOMAIN_TRADING

logger = logging.getLogger("data_platform.config_store")

KIND_DATA = DOMAIN_DATA          # 数据源（拉取侧）
KIND_TRADING = DOMAIN_TRADING    # 交易账号（下单/行情侧）
KINDS = (KIND_DATA, KIND_TRADING)
LABEL = {KIND_DATA: "数据源", KIND_TRADING: "交易账号"}

# 写入白名单列序（唯一真源：`_write_cols` 按 values 的键取子集，INSERT/UPDATE 共用）。
# 数据源域**无** exchanges/account_key——合表两列随交易侧迁走（83a 表结构立法）。
_WRITE_COLS = {
    KIND_DATA: ("name", "provider", "market", "credentials_encrypted", "params",
                "capabilities", "enabled"),
    KIND_TRADING: ("name", "provider", "market", "exchanges", "account_key",
                   "credentials_encrypted", "params", "capabilities", "enabled"),
}

# 读侧列（顺序与 row_dict 解包一致；两族差 exchanges/account_key 两列）
_READ_COLS = {
    KIND_DATA: ("id, name, provider, market, credentials_encrypted IS NOT NULL, "
                "params, capabilities, position, enabled, updated_at"),
    KIND_TRADING: ("id, name, provider, market, exchanges, credentials_encrypted IS NOT NULL, "
                   "params, capabilities, position, enabled, updated_at, account_key"),
}


def _tbl(kind: str) -> str:
    """域 → 表名（83a 立法：域=表，无第二字面量）。未知域=编程错误，硬失败（防拼接越界）。"""
    if kind not in KINDS:
        raise ValueError(f"未知配置域 {kind!r}（仅 {KINDS}）")
    return kind


def is_unique_violation(e: Exception) -> bool:
    """psycopg3 唯一约束冲突（SQLSTATE 23505）——account_key UNIQUE(provider,account_key) 判重。"""
    return getattr(e, "sqlstate", None) == "23505"


def is_fk_violation(e: Exception) -> bool:
    """psycopg3 外键约束冲突（SQLSTATE 23503）——delete 守卫兜底。"""
    return getattr(e, "sqlstate", None) == "23503"


# --- 读 ---

def row_dict(r, kind: str, with_code_caps: bool = True) -> dict:
    """DB 行 → API 形状（params=jsonb 读侧已是 dict；附 code_capabilities=能力真源）。

    两族列集不同（交易族多 exchanges/account_key）——按 kind 取值，另一族键给 None
    （前端表格/弹窗按 kind 决定是否渲染，形状统一免分叉）。
    """
    provider = r[2]
    if kind == KIND_TRADING:
        d = {"id": r[0], "name": r[1], "provider": provider, "market": r[3],
             "exchanges": list(r[4]) if r[4] else None,
             "has_credentials": bool(r[5]), "params": r[6],
             "capabilities": list(r[7]), "position": r[8], "enabled": r[9],
             "updated_at": str(r[10]) if r[10] else None,
             "account_key": r[11]}
    else:
        d = {"id": r[0], "name": r[1], "provider": provider, "market": r[3],
             "exchanges": None, "has_credentials": bool(r[4]), "params": r[5],
             "capabilities": list(r[6]), "position": r[7], "enabled": r[8],
             "updated_at": str(r[9]) if r[9] else None, "account_key": None}
    if with_code_caps:
        from src.data_platform.capabilities import provider_capabilities
        d["code_capabilities"] = sorted(provider_capabilities(provider))
    return d


def list_rows(kind: str, cap: str | None = None) -> list:
    """本域行原始元组列表（cap=GIN 包含式过滤；值域校验在路由层做，避免静默空表）。"""
    sql = f"SELECT {_READ_COLS[kind]} FROM {_tbl(kind)}"
    args: tuple = ()
    if cap:
        sql += " WHERE capabilities @> ARRAY[%s]::text[]"
        args = (cap,)
    sql += " ORDER BY position, id"
    with get_conn() as conn:
        return conn.execute(sql, args).fetchall()


def get_conn_row(kind: str, iid: int) -> tuple | None:
    """本域单行（provider, credentials_encrypted, params, capabilities）——连接测试用。"""
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT provider, credentials_encrypted, params, capabilities "
            f"FROM {_tbl(kind)} WHERE id=%s", (iid,))
        return cur.fetchone()


def row_exists(kind: str, iid: int) -> bool:
    """本域是否存在该 id 行（改前存在性检查）。"""
    with get_conn() as conn:
        cur = conn.execute(f"SELECT id FROM {_tbl(kind)} WHERE id=%s", (iid,))
        return cur.fetchone() is not None


def all_ids(kind: str) -> set[int]:
    """本表全部 id（拖拽全量校验用——防并发丢行/幽灵 id）。"""
    with get_conn() as conn:
        cur = conn.execute(f"SELECT id FROM {_tbl(kind)}")
        return {r[0] for r in cur.fetchall()}


def has_live_task(account_id: int) -> bool:
    """该 id 是否被 live_task 引用（**仅交易族可调**：live_task.account_id → trading_account.id）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT id FROM live_task WHERE account_id=%s LIMIT 1", (account_id,))
        return cur.fetchone() is not None


def load_provider_params(provider: str) -> tuple[int, dict] | None:
    """读数据源 provider 的配置行（enabled 优先，表内 position 序）→ (id, params dict)；无=None。

    批 83a：原 external_interface「数据域谓词」选行 → `data_source` 表直读（表本身即数据域）。
    限流四层（rate_limits/熔断/pacer）经此读写（终裁三消费方①）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT id, params FROM {_tbl(KIND_DATA)} WHERE provider=%s "
            "ORDER BY enabled DESC, position, id LIMIT 1", (provider,))
        r = cur.fetchone()
    if not r:
        return None
    if isinstance(r[1], dict):
        return r[0], r[1]          # params=jsonb（psycopg 读侧已 dict）
    try:
        return r[0], (json.loads(r[1]) if r[1] else {})
    except (TypeError, ValueError):
        logger.warning("data_source(%s) params 非法 JSON，按空处理", provider)
        return r[0], {}


# --- 写 ---

def _write_cols(kind: str, values: dict) -> list[str]:
    """写入列 = 白名单列序 ∩ values 键（省略的列不写——credentials 省略=不改，三段语义）。"""
    return [c for c in _WRITE_COLS[kind] if c in values]


def _ph(col: str) -> str:
    return f"{col}=%s::jsonb" if col == "params" else f"{col}=%s"


def insert_row(kind: str, values: dict) -> int:
    """新增本域行，position=**本表** max+1（单表单序列），返回新 id。

    两表 id 各自独立序列；position 亦各表独立（原「全局单序列」随拆表退役）。
    异常显式 rollback（沿用路由层原语义：失败不留半事务）。
    """
    tbl = _tbl(kind)
    cols = _write_cols(kind, values)
    sql = (f"INSERT INTO {tbl} ({', '.join(cols)}, position) "
           f"VALUES ({', '.join(_ph(c) for c in cols)}, "
           f"(SELECT coalesce(max(position),-1)+1 FROM {tbl})) RETURNING id")
    with get_conn() as conn:
        try:
            cur = conn.execute(sql, tuple(values[c] for c in cols))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return cur.fetchone()[0]


def update_row(kind: str, iid: int, values: dict) -> None:
    """改本域行（仅写 values 提供的列；credentials_encrypted 缺席=不改）。"""
    tbl = _tbl(kind)
    cols = _write_cols(kind, values)
    sets = ", ".join(_ph(c) for c in cols)
    with get_conn() as conn:
        try:
            conn.execute(f"UPDATE {tbl} SET {sets}, updated_at=now() WHERE id=%s",
                         tuple(values[c] for c in cols) + (iid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def delete_row(kind: str, iid: int) -> None:
    """删本域行（是否存在由调用方先查；FK 冲突由调用方映射错误码）。"""
    with get_conn() as conn:
        try:
            conn.execute(f"DELETE FROM {_tbl(kind)} WHERE id=%s", (iid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def set_positions(kind: str, ids: list[int]) -> None:
    """按提交序重编号 position=0..n-1（单表内）。"""
    tbl = _tbl(kind)
    with get_conn() as conn:
        try:
            for pos, rid in enumerate(ids):
                conn.execute(f"UPDATE {tbl} SET position=%s, updated_at=now() WHERE id=%s", (pos, rid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def save_provider_params(row_id: int, params: dict) -> None:
    """数据源行 params 整体写回（读-改-写）。双盲补审修正：有 last-writer-wins 窗口
    （两 admin 并发、或 cb 与 rate_limits 两端点并发丢一边修改）——admin 低频可接受，
    根治需 SELECT FOR UPDATE 同事务。
    """
    with get_conn() as conn:
        try:
            conn.execute(f"UPDATE {_tbl(KIND_DATA)} SET params=%s::jsonb, updated_at=now() "
                         "WHERE id=%s", (json.dumps(params, ensure_ascii=False), row_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# --- account_permission（D1：account 侧品种权限） ---

_PERM_COLS = ("account_id, allowed_categories, allowed_exchanges, allowed_boards, "
              "is_st_allowed, convertible_allowed")


def get_account_permission(account_id: int) -> tuple | None:
    """权限读（无行=None——权限人工配置，未配置不猜默认）。"""
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_PERM_COLS} FROM account_permission WHERE account_id=%s", (account_id,))
        return cur.fetchone()


def upsert_account_permission(account_id: int, categories, exchanges, boards,
                              is_st_allowed: bool, convertible_allowed: bool) -> None:
    """权限写（upsert）。account 存在性由调用方先验（须为 trading_account 行）。"""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO account_permission (account_id, allowed_categories, allowed_exchanges, "
                "allowed_boards, is_st_allowed, convertible_allowed) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (account_id) DO UPDATE SET "
                "allowed_categories=EXCLUDED.allowed_categories, "
                "allowed_exchanges=EXCLUDED.allowed_exchanges, "
                "allowed_boards=EXCLUDED.allowed_boards, "
                "is_st_allowed=EXCLUDED.is_st_allowed, "
                "convertible_allowed=EXCLUDED.convertible_allowed, updated_at=now()",
                (account_id, list(categories), list(exchanges), list(boards),
                 is_st_allowed, convertible_allowed))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
