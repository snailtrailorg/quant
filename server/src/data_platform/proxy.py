"""批 102a：代理配置面数据访问 + 解析（层 1）——**全部 SQL 在此**。

设计真源：`flow/方案/多市场数据接入与代理体系-设计-20261006.md` §三。
路由层（`web_api/routes/proxies.py`，层 4）只做参数校验/错误码映射/审计——SQL 零内联
（守门 `tests/test_no_sql_in_new_routes.py` 的白名单外新文件档：禁 `get_conn` + 禁 SQL 字面量）。

**两表**（迁移 0135）：`proxy_config`（全局池）+ `proxy_binding`（按消费方绑定，enable/select 两选项）。

**⭐ scheme 词表＝{socks5h, socks5, http, https}**（威廉姆 2026-10-06 裁定不锁死 SOCKS）。
真正的硬约束不是 scheme 名字，而是 **DNS 在哪一侧解析**：

| scheme | 出口 | DNS 解析侧 | 被污染域名（prod 上 OKX→169.254.0.2） |
|---|---|---|---|
| `socks5h://` | SOCKS5 | **代理侧** | ✅ 可用 |
| `http://` / `https://` | HTTP(S) CONNECT | **代理侧**（https 目标走 `CONNECT host:port`；http 目标转发绝对 URI） | ✅ 可用 |
| `socks5://` | SOCKS5 | **本地** | ❌ 必死 |

⇒ 词表**放行** `socks5://`（本地 DNS 可用的内网场景合法），但 UI 提示 + probe 实测把关；
**不在 DDL 里武断封杀一个个例**。

**依赖**：`http(s)://` 是 requests 原生（不需要 PySocks）；`socks*://` 才需要 PySocks。

**禁进程级缓存**：本模块的解析函数**无任何模块级缓存**——每任务启动解析一次即可，
写进程缓存会在「测试打桩 DB」后污染后续用例（见 MEMORY 的既有坑）。
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse, urlunparse

import requests

from src.data_platform.db import get_conn

logger = logging.getLogger("data_platform.proxy")

ALLOWED_SCHEMES = frozenset({"socks5h", "socks5", "http", "https"})

# 本地解析 DNS 的 scheme——凡绑定到「域名被污染」的消费方都要避开（仅告警，不拒）
LOCAL_DNS_SCHEMES = frozenset({"socks5"})

# 探测出口 IP 的默认目标（运维一键诊断用；可用 target 覆盖成真实 provider 端点）
PROBE_DEFAULT_TARGET = "https://api.ipify.org?format=json"


# ─────────────────────────── 纯函数：校验 / 掩码 ───────────────────────────


def validate_url(url: str) -> str:
    """校验代理 DSN，返回规范化值（strip 后）。**词表外响亮拒绝**（ValueError）。

    要求：scheme ∈ ALLOWED_SCHEMES；有 host；**有显式端口**（代理无端口＝配置残废，
    默认端口在不同代理实现上不一致，宁可让运维写明确）。
    """
    s = str(url or "").strip()
    if not s:
        raise ValueError("代理 url 不能为空")
    p = urlparse(s)
    if p.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(
            f"代理 scheme {p.scheme!r} 不支持（支持 {sorted(ALLOWED_SCHEMES)}）")
    if not p.hostname:
        raise ValueError(f"代理 url 缺 host：{s!r}")
    try:
        _ = p.port
    except ValueError as e:                                  # 非数字端口
        raise ValueError(f"代理 url 端口非法：{s!r}（{e}）")
    if p.port is None:
        raise ValueError(f"代理 url 须带显式端口：{s!r}（如 socks5h://host:1080）")
    return s


def validate_endpoint(ep: str) -> str:
    """校验端点覆盖（http/https + host）。空串 → 空串（＝清除覆盖）。"""
    s = str(ep or "").strip()
    if not s:
        return ""
    p = urlparse(s)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"端点覆盖须为 http(s):// 绝对地址：{s!r}")
    if not p.hostname:
        raise ValueError(f"端点覆盖缺 host：{s!r}")
    return s


def mask_url(url: str) -> str:
    """**仅替 userinfo**，scheme/host/port 原样（与凭证掩码同模式）。

    `socks5h://u:p@h:1080` → `socks5h://***@h:1080`；无 userinfo 则原样返回。
    """
    try:
        p = urlparse(str(url))
        if not (p.username or p.password):
            return str(url)
        netloc = p.hostname or ""
        if p.port:
            netloc = f"{netloc}:{p.port}"
        return urlunparse((p.scheme, f"***@{netloc}", p.path, p.params, p.query, p.fragment))
    except Exception:                                        # 掩码是防御性展示，绝不因解析失败泄漏原文
        return "***"


def is_local_dns(url: str) -> bool:
    """该代理是否在**本地**解析 DNS（＝对污染域名不可用）。"""
    try:
        return urlparse(str(url)).scheme.lower() in LOCAL_DNS_SCHEMES
    except Exception:
        return False


def proxies_map(dsn: str | None) -> dict | None:
    """requests 的 `proxies` 参数；`None` ＝**直连**。

    **唯一真源**（批 102b 收编：原散在 `binance_adapter._proxies`）——okx 及其后每个
    自持 HTTP 的 adapter 都用它，避免「两处各写一遍 map」日后一处改一处忘。

    两路走**同一个 map**（形态统一，依赖面不同）：`http(s)://` 代理是 requests 原生；
    `socks5h://`/`socks5://` 由 **PySocks** 提供传输层。
    """
    return {"http": dsn, "https": dsn} if dsn else None


# ─────────────────────────── 代理池 CRUD ───────────────────────────


def list_proxies(mask: bool = True) -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT name, url, notes, enabled, created_at, updated_at "
            "FROM proxy_config ORDER BY name")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["local_dns"] = is_local_dns(r["url"])
        if mask:
            r["url"] = mask_url(r["url"])
        r["created_at"] = str(r["created_at"])
        r["updated_at"] = str(r["updated_at"])
    return rows


def get_proxy_raw(name: str) -> dict | None:
    """**未掩码**单行（仅供 probe 等内部使用，绝不出 API）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT name, url, notes, enabled FROM proxy_config WHERE name=%s", (name,))
        r = cur.fetchone()
    if not r:
        return None
    return {"name": r[0], "url": r[1], "notes": r[2], "enabled": r[3]}


def upsert_proxy(name: str, url: str, notes: str | None = None,
                 enabled: bool = True) -> None:
    """新增/更新一行。**`url` 空串＝留空不改**（与 SMTP 密码同款三段语义，批 38 先例）。

    为什么必须有「留空不改」这一段：`url` 是**唯一含凭证**的列，而 `list_proxies` 只回
    **掩码值**（`socks5h://***@h:1080`）⇒ 若前端编辑时把掩码值原样回写，`validate_url` 会
    因 userinfo 里出现 `***` 而**静默毁掉代理凭证**（或让认证失败变成难查的运行时错）。
    故：空 url ⇒ 保留原值；**新建行缺 url ⇒ 响亮拒绝**（不能建一条没有出口的代理）。
    """
    n = str(name or "").strip()
    if not n:
        raise ValueError("代理 name 不能为空")
    s = str(url or "").strip()
    with get_conn() as conn:
        if not s:
            cur = conn.execute("SELECT 1 FROM proxy_config WHERE name=%s", (n,))
            if cur.fetchone() is None:
                raise ValueError("新建代理必须提供 url（socks5h://host:port 等）")
            conn.execute(
                "UPDATE proxy_config SET notes=%s, enabled=%s, updated_at=now() WHERE name=%s",
                (notes, bool(enabled), n))
            conn.commit()
            return
        clean = validate_url(s)
        conn.execute(
            "INSERT INTO proxy_config (name, url, notes, enabled) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE SET url=EXCLUDED.url, notes=EXCLUDED.notes, "
            "enabled=EXCLUDED.enabled, updated_at=now()",
            (n, clean, notes, bool(enabled)))
        conn.commit()


def set_proxy_enabled(name: str, enabled: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE proxy_config SET enabled=%s, updated_at=now() WHERE name=%s "
            "RETURNING name", (bool(enabled), name))
        ok = cur.fetchone() is not None
        conn.commit()
    return ok


def delete_proxy(name: str) -> bool:
    """删池中一行。被绑定引用时 FK 是 `ON DELETE SET NULL`——绑定行的 proxy_name 变 NULL；
    若该绑定 `proxy_enabled=true`，CHECK 会拒（`NOT enabled OR name IS NOT NULL`）⇒
    **先关绑定再删**，DB 层强制，不会留下「启用了但没有代理」的悬空态。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM proxy_config WHERE name=%s RETURNING name", (name,))
        ok = cur.fetchone() is not None
        conn.commit()
    return ok


# ─────────────────────────── 绑定读写 ───────────────────────────


def list_bindings() -> list[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT consumer, proxy_enabled, proxy_name, endpoint_override, updated_at "
            "FROM proxy_binding ORDER BY consumer")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["updated_at"] = str(r["updated_at"])
    return rows


def set_binding(consumer: str, proxy_enabled: bool, proxy_name: str | None,
                endpoint_override: str | None = None) -> None:
    """写入一条绑定。语义（设计 §3.3）：`proxy_enabled=false` 或 `proxy_name` 空 ⇒ 直连。"""
    c = str(consumer or "").strip()
    if not c:
        raise ValueError("consumer 不能为空")
    pn = (str(proxy_name).strip() or None) if proxy_name is not None else None
    eo = validate_endpoint(endpoint_override or "")
    if proxy_enabled and not pn:
        raise ValueError("启用代理必须选一个池中代理（enable 与 select 是两件事，但启用时 select 不可空）")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO proxy_binding (consumer, proxy_enabled, proxy_name, endpoint_override) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (consumer) DO UPDATE SET proxy_enabled=EXCLUDED.proxy_enabled, "
            "proxy_name=EXCLUDED.proxy_name, endpoint_override=EXCLUDED.endpoint_override, "
            "updated_at=now()",
            (c, bool(proxy_enabled), pn, eo or None))
        conn.commit()


def delete_binding(consumer: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM proxy_binding WHERE consumer=%s RETURNING consumer",
                           (consumer,))
        ok = cur.fetchone() is not None
        conn.commit()
    return ok


# ─────────────────────────── 解析（同步链路的出口） ───────────────────────────


def _load_binding(consumer: str) -> tuple[bool, str | None, str | None, str | None] | None:
    """`(proxy_enabled, proxy_name, proxy_url, proxy_row_enabled)`；无绑定行 → None。

    `proxy_url`/`proxy_row_enabled` 来自 LEFT JOIN，池中无该行时为 None（悬空绑定）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT b.proxy_enabled, b.proxy_name, p.url, p.enabled "
            "FROM proxy_binding b LEFT JOIN proxy_config p ON p.name = b.proxy_name "
            "WHERE b.consumer=%s", (consumer,))
        r = cur.fetchone()
    return (r[0], r[1], r[2], r[3]) if r else None


def resolve_proxy(consumer: str) -> str | None:
    """该消费方的出口代理 DSN；`None` ＝ **直连**。

    条件（全满足才返回）：绑定行存在 ∧ `proxy_enabled` ∧ `proxy_name` 非空 ∧
    池中有该行 ∧ 该行 `enabled=true`。返回**池中 url 原样**（scheme 由运维选定，本层不加工）。

    **无缓存**：每次调用读库。调用方按「每任务一次」的自然节奏调用即可。
    """
    if not consumer:
        return None
    b = _load_binding(str(consumer))
    if not b:
        return None
    enabled, name, url, row_enabled = b
    if not enabled or not name or not url or not row_enabled:
        return None
    return url


def _load_endpoint(consumer: str) -> str | None:
    """端点覆盖原文（未 strip）；无绑定行或为空 → None。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT endpoint_override FROM proxy_binding WHERE consumer=%s", (consumer,))
        r = cur.fetchone()
    if not r or not r[0]:
        return None
    return str(r[0]).strip() or None


def resolve_endpoint(consumer: str) -> str | None:
    """该消费方的**端点覆盖**；`None` ＝ 用代码缺省。与是否启用代理**无关**（两个独立维）。"""
    if not consumer:
        return None
    return _load_endpoint(str(consumer))


def capable_consumers() -> list[str]:
    """**可配代理的消费方**＝ adapter 类声明了 `supports_exit_config` 的 provider。

    这是「配了会不会生效」的判据：SDK 型 adapter（tushare/聚宽官方客户端不暴露 per-call
    代理）没接线 ⇒ 为其写绑定是**静默无效**的配置，路由层据此**响亮拒绝**（400）。
    随 adapter 接线扩展（102b 的 okx）本集合自动长大——**唯一真源＝adapter 类属性**。
    """
    from src.data_platform.adapters.base import list_adapters
    return sorted(p for p, cls in list_adapters().items()
                  if getattr(cls, "supports_exit_config", False))


# ─────────────────────────── 探连通（运维一键诊断） ───────────────────────────


def probe(name: str, target: str | None = None, timeout: int = 15) -> dict:
    """经该代理 GET `target`（默认 ipify）→ 出口 IP + 延迟。

    **这是「代理是否真能出网」的唯一权威判据**：`socks5://`（本地 DNS）在污染域上会
    表现为连接错误，probe 会如实报错而不是静默通过。

    返回 `{"ok", "exit_ip", "elapsed_ms", "status", "error"}`；**失败不抛**（诊断端点，
    失败信息本身就是结果）。
    """
    row = get_proxy_raw(name)
    if not row:
        return {"ok": False, "error": f"代理 {name} 不存在"}
    url = row["url"]
    tgt = target or PROBE_DEFAULT_TARGET
    proxies = {"http": url, "https": url}
    t0 = time.monotonic()
    try:
        resp = requests.get(tgt, proxies=proxies, timeout=timeout,
                            headers={"User-Agent": "quant-proxy-probe/1.0"})
        elapsed = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "elapsed_ms": elapsed,
                    "error": f"目标返回 {resp.status_code}"}
        ip = None
        try:
            ip = resp.json().get("ip")
        except Exception:
            ip = (resp.text or "").strip()[:64] or None
        return {"ok": True, "exit_ip": ip, "status": resp.status_code,
                "elapsed_ms": elapsed, "error": None}
    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "elapsed_ms": elapsed, "error": f"{type(e).__name__}: {str(e)[:160]}"}
