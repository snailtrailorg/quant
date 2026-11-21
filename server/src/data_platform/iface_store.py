"""批189 接口实体数据层（层 1）——三实体 CRUD / 启用校验 / 映射 / 物理删。

契约：批189-M0-契约.md v2 §1/§2/§5；路由层只做鉴权与错误映射，SQL 全在本模块。
不 import 层 2（载体探针由调用方注入）。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from src.data_platform.db import get_conn

CODE_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")

KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "md": ("market_interface", "md-", ("name", "provider", "market", "account_key",
                                      "login_key", "credentials_encrypted", "params",
                                      "impl", "position", "enabled", "exchanges")),
    "td": ("trade_interface", "td-", ("name", "provider", "market", "account_key",
                                     "login_key", "credentials_encrypted", "params",
                                     "impl", "position", "enabled", "is_virtual")),
    "ds": ("data_source", "ds-", ("name", "provider", "market", "credentials_encrypted",
                                 "params", "capabilities", "impl", "position", "enabled")),
}


class IfaceError(Exception):
    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(detail or code)
        self.code, self.status, self.detail = code, status, detail


def _tbl(kind: str) -> tuple[str, str, tuple[str, ...]]:
    if kind not in KINDS:
        raise IfaceError("KIND_UNKNOWN", 400, f"kind={kind}")
    return KINDS[kind]


def check_code(kind: str, code: str) -> None:
    t, prefix, _ = _tbl(kind)
    if not CODE_RE.match(code or ""):
        raise IfaceError("CODE_INVALID", 400, f"code={code!r} 不合 ^[a-z][a-z0-9-]{{1,30}}$")
    if not code.startswith(prefix):
        raise IfaceError("CODE_INVALID", 400, f"code 必须以 {prefix} 开头")


def _read_projection(cols: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """读取投影：凭证列只回 has_credentials 布尔（沿用 config_store 口径，永不回显密文）。"""
    exprs, keys = [], []
    for c in cols:
        if c == "credentials_encrypted":
            exprs.append("credentials_encrypted IS NOT NULL AS has_credentials")
            keys.append("has_credentials")
        else:
            exprs.append(c)
            keys.append(c)
    return ",".join(exprs), tuple(keys)


def list_interfaces(kind: str, *, enabled: bool | None = None, market: str | None = None) -> list[dict]:
    t, _, cols = _tbl(kind)
    proj, keys = _read_projection(cols)
    sql = f"SELECT code,{proj} FROM {t} WHERE 1=1"
    args: list[Any] = []
    if enabled is not None:
        sql += " AND enabled=%s"
        args.append(enabled)
    if market:
        sql += " AND market=%s"
        args.append(market)
    sql += " ORDER BY position, code"
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [dict(zip(("code",) + keys, r)) for r in rows]


def get_interface(kind: str, code: str) -> dict:
    t, _, cols = _tbl(kind)
    proj, keys = _read_projection(cols)
    with get_conn() as conn:
        r = conn.execute(f"SELECT code,{proj} FROM {t} WHERE code=%s", (code,)).fetchone()
    if not r:
        raise IfaceError("IFACE_NOT_FOUND", 404, code)
    return dict(zip(("code",) + keys, r))


def get_td_identity(td_code: str) -> dict | None:
    """M5 身份解析：按 td_code 取交易接口身份字段（含 login_key/凭证/impl）。

    供 TD 执行链路在平台层只用 code、把物理账号解析留在券商会话层。
    凭证密文在此返回（调用方按 impl 决定是否解密），**不回显 UI**。缺行 None。
    """
    with get_conn() as conn:
        r = conn.execute(
            "SELECT code,provider,account_key,login_key,credentials_encrypted,params,impl,is_virtual,enabled "
            "FROM trade_interface WHERE code=%s", (str(td_code),)).fetchone()
    if not r:
        return None
    return dict(zip(("code", "provider", "account_key", "login_key",
                     "credentials_encrypted", "params", "impl", "is_virtual", "enabled"), r))


def resolve_login_key(account: str) -> str:
    """code → login_key（account_permission 归属键）；非 code（已是 login_key）原样返回。"""
    ident = get_td_identity(account)
    return ident["login_key"] if ident else str(account)


def td_sibling_codes(login_key: str) -> list[str]:
    """D-M0-6：同 login_key 的所有 TD code（字典序）——供终端号稳定分配。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT code FROM trade_interface WHERE login_key=%s ORDER BY code",
            (login_key,)).fetchall()
    return [r[0] for r in rows]


def _login_key(kind: str, provider: str, account_key: str | None, creds: str | None,
               is_virtual: bool = False) -> str:
    if is_virtual:
        return f"{provider}|virtual"
    if account_key:
        return f"{provider}|{account_key}"
    if kind == "md" and not creds:
        return f"{provider}|public"      # 公开行情（无凭证）
    return f"{provider}|legacy"          # M0 §1：同 provider 仅一行空 key 时回落


def create_interface(kind: str, payload: dict, carrier_probe: Callable[[str, str], bool]) -> dict:
    t, _, cols = _tbl(kind)
    code = (payload.get("code") or "").strip()
    check_code(kind, code)
    data = {c: payload.get(c) for c in cols if c in payload}
    if kind != "ds":
        data["login_key"] = _login_key(kind, data.get("provider", ""), data.get("account_key"),
                                       data.get("credentials_encrypted"), bool(data.get("is_virtual")))
    data.setdefault("enabled", False)
    if data.get("enabled"):
        validate_payload(kind, {**data, "code": code}, carrier_probe)
    keys = ["code"] + list(data)
    sql = f"INSERT INTO {t} ({','.join(keys)}) VALUES ({','.join(['%s'] * len(keys))}) RETURNING code"
    try:
        with get_conn() as conn:
            conn.execute(sql, tuple([code] + [data[k] for k in data]))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        if getattr(e, "sqlstate", None) == "23505" or getattr(e, "pgcode", "") == "23505":
            raise IfaceError("CODE_CONFLICT", 409, code) from e
        raise
    return get_interface(kind, code)


def update_interface(kind: str, code: str, patch: dict,
                     carrier_probe: Callable[[str, str], bool]) -> dict:
    t, _, cols = _tbl(kind)
    cur = get_interface(kind, code)          # 不存在 → 404
    if "code" in patch and patch["code"] != code:
        raise IfaceError("CODE_IMMUTABLE", 400, "code 不可修改")
    upd = {c: patch[c] for c in cols if c in patch}
    merged = {**cur, **upd}
    if kind != "ds":
        upd["login_key"] = _login_key(kind, merged.get("provider", ""), merged.get("account_key"),
                                      merged.get("credentials_encrypted"), bool(merged.get("is_virtual")))
        merged["login_key"] = upd["login_key"]
    if merged.get("enabled"):
        validate_payload(kind, merged, carrier_probe)
    if not upd:
        return cur
    sets = ",".join(f"{k}=%s" for k in upd)
    with get_conn() as conn:
        conn.execute(f"UPDATE {t} SET {sets}, updated_at=now() WHERE code=%s",
                     tuple(list(upd.values()) + [code]))
        conn.commit()
    return get_interface(kind, code)


def validate_payload(kind: str, row: dict, carrier_probe: Callable[[str, str], bool]) -> None:
    """启用前校验（建/改/启用共用）。失败不改 enabled。"""
    role = "market" if kind == "md" else "trade"
    if not carrier_probe(role, row.get("provider", "")):
        raise IfaceError("PROVIDER_UNSUPPORTED", 400, f"{kind}:{row.get('provider')}")
    if kind != "ds" and not row.get("is_virtual"):
        if not row.get("credentials_encrypted") and kind == "td":
            raise IfaceError("CREDENTIALS_INCOMPLETE", 400, "交易接口缺凭证")
    if not row.get("login_key"):
        raise IfaceError("CREDENTIALS_INCOMPLETE", 400, "login_key 缺失")
    # |legacy 只允许迁移期产生；运行期启用必须有稳定物理账号键
    if not row.get("account_key") and not (kind == "md" and not row.get("credentials_encrypted")):
        raise IfaceError("CREDENTIALS_INCOMPLETE", 400, "account_key 为空（仅迁移期允许 legacy）")


def delete_interface(kind: str, code: str, stop_unit: Callable[[str], None],
                     is_active: Callable[[str], bool]) -> None:
    t, _, _ = _tbl(kind)
    row = get_interface(kind, code)
    with get_conn() as conn:
        if kind == "td":
            if row.get("is_virtual"):
                raise IfaceError("IFACE_BUSY", 409, "虚拟账户不可删")
            n = conn.execute("SELECT count(*) FROM live_task WHERE account_code=%s", (code,)).fetchone()[0]
            if n:
                raise IfaceError("IFACE_BUSY", 409, f"{n} 个任务引用")
    unit = ("quant-md-hub@" if kind == "md" else "quant-td-exec@") + code + ".service"
    try:
        stop_unit(unit)
    except Exception as e:  # noqa: BLE001
        raise IfaceError("IFACE_IDLE", 409, f"停进程失败: {e}") from e
    if is_active(unit):
        raise IfaceError("IFACE_IDLE", 409, "进程未退出，拒绝删除")
    with get_conn() as conn:
        if kind == "td":
            conn.execute("DELETE FROM td_md_map WHERE td_code=%s", (code,))
            for tb in ("position_snapshot", "position_refresh", "account_snapshot"):
                conn.execute(f"DELETE FROM {tb} WHERE account_code=%s", (code,))
            conn.execute("DELETE FROM provider_session_slot WHERE login_key=%s AND role='td'", (row["login_key"],))
        else:
            conn.execute("DELETE FROM td_md_map WHERE md_code=%s", (code,))
        conn.execute(f"DELETE FROM {t} WHERE code=%s", (code,))
        conn.commit()


def set_mappings(td_code: str, items: Iterable[dict]) -> None:
    with get_conn() as conn:
        for it in items:
            chk = conn.execute("SELECT market FROM trade_interface WHERE code=%s", (td_code,)).fetchone()
            mkt = conn.execute("SELECT market FROM market_interface WHERE code=%s", (it["md_code"],)).fetchone()
            if not chk or not mkt:
                raise IfaceError("MAPPING_MISSING", 409, "td 或 md 不存在")
            if chk[0] != mkt[0]:
                raise IfaceError("MAPPING_MISSING", 409, "跨市场映射不允许")
        conn.execute("DELETE FROM td_md_map WHERE td_code=%s", (td_code,))
        for it in items:
            conn.execute("INSERT INTO td_md_map(td_code,md_code,priority) VALUES (%s,%s,%s)",
                         (td_code, it["md_code"], int(it["priority"])))
        conn.commit()


def list_mappings(kind: str, code: str) -> list[dict]:
    with get_conn() as conn:
        if kind == "td":
            rows = conn.execute("SELECT md_code,priority FROM td_md_map WHERE td_code=%s ORDER BY priority",
                                (code,)).fetchall()
            return [{"md_code": r[0], "priority": r[1]} for r in rows]
        rows = conn.execute("SELECT td_code,priority FROM td_md_map WHERE md_code=%s ORDER BY priority, td_code",
                            (code,)).fetchall()
        return [{"td_code": r[0], "priority": r[1]} for r in rows]
