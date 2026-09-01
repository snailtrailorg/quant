"""系统维护相关端点：健康检查 / 探针 / 系统配置 / 通知 / 邮件 / 条款 / 帮助。"""

from fastapi import APIRouter, Depends, Request, Body, BackgroundTasks
from ..auth import require_role, require_perm, audit_log
from ..errors import ApiError
from ..models import (LoginReq, UserCreate, StrategyConfig, InviteReq, RegisterReq, ForgotReq, ResetReq, ChangePwdReq, LogAnalyzeReq, ChatReq, LLMModelReq, IMBotCreateReq, IMBotUpdateReq, IMBotUserReq, LlmBudgetReq, DataSourceReq, ChannelReq, BrokerReq, RiskRuleReq, PoolReq, StrategyAccountReq)
from src.data_platform.db import get_conn
from src.email_service import queue_email, try_row
from ..terms import get_terms_items
import os
import json
import logging
logger = logging.getLogger("web_api")

router = APIRouter(tags=["system"])

# ——— 操作指导书（链条打磨批次 4：Web 内置帮助）———

_GUIDE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "操作指导")  # server/docs/操作指导（随 rsync 部署；根 docs/ 不上传）
_GUIDES = {"index": "索引.md", "factors": "01-因子.md", "strategy": "02-策略.md",
           "backtest": "03-回测.md", "live": "04-实盘.md"}


@router.get("/api/help/{topic}")
def get_help_api(topic: str,
                 payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """操作指导书内容（markdown 原文，前端渲染）。topic: index/factors/strategy/backtest/live。"""
    fname = _GUIDES.get(topic)
    if not fname:
        raise ApiError(404, "HELP_NOT_FOUND", f"未知帮助主题: {topic}")
    path = os.path.join(_GUIDE_DIR, fname)
    try:
        with open(path, encoding="utf-8") as f:
            return {"topic": topic, "content": f.read()}
    except FileNotFoundError:
        return {"topic": topic, "content": "# 帮助内容未找到\n\n指导书文件缺失，请检查部署。", "missing": True}


@router.get("/api/runbook")
def runbook_api(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """通知 runbook 映射（W3 单源：站内 chip/处置行消费）。暂仅中文（多语言债）。"""
    from src.alert_notify.runbook import RUNBOOK
    return {"items": RUNBOOK}


@router.get("/api/_probe")
def api_probe(request: Request):
    """部署管道冒烟探针（W2，P1-2 2026-09-01）：只读六检查聚合——进程活≠功能对
    （backtest 500 类运行时错误 healthz/readyz 拦不住）。免账号免密钥：管道 localhost
    直连（无代理头）；外封=nginx /readyz 同待遇 allowlist（装位前内卫兜底）。

    计数判据：ok=查询成功，计数仅回显，**0=ok**（staging 空库首跑不误红——回滚好版本
    是最大风险，盲审 A-P1）。内卫：X-Real-IP 非私网/畸形头 → 403 fail-closed。
    """
    import ipaddress
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        try:
            if not ipaddress.ip_address(real_ip).is_private:
                raise ApiError(403, "PROBE_FORBIDDEN", "探针仅内网/本机")
        except ValueError:
            raise ApiError(403, "PROBE_FORBIDDEN", "探针仅内网/本机")   # 畸形头 fail-closed

    checks: dict = {}

    def _chk(name, fn):
        try:
            checks[name] = f"ok:{fn()}"
        except Exception as e:
            checks[name] = f"fail: {str(e)[:60]}"

    def _db():
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return "reachable"

    def _count(sql):
        def _q():
            with get_conn() as conn:
                return conn.execute(sql).fetchone()[0]
        return _q

    def _valkey():
        import os, redis
        r = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), socket_timeout=2)
        r.ping()
        return "pong"

    def _hub_hb():
        import os, redis
        r = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"), socket_timeout=2)
        ttl = r.ttl("quant:hb:md-hub")
        if ttl is None or ttl <= 0:
            raise RuntimeError(f"hub 心跳不在场 (ttl={ttl})")
        return f"ttl={ttl}s"

    def _factors():
        from src.strategy_framework.factor import list_factors
        return len(list_factors())

    _chk("db", _db)
    _chk("factors", _factors)
    _chk("strategy_config", _count("SELECT count(*) FROM strategy_config"))
    _chk("notifications", _count("SELECT count(*) FROM notifications"))
    _chk("valkey", _valkey)
    _chk("hub_hb", _hub_hb)
    ok = all(v.startswith("ok") for v in checks.values())
    return {"ok": ok, "checks": checks}


@router.get("/healthz")
@router.get("/health")   # 兼容旧路径
def healthz():
    """liveness：进程活着即 ok（不查依赖）。nginx/Zabbix 外部探活用。"""
    return {"status": "ok", "version": "0.1.0"}


@router.get("/readyz")
def readyz():
    """readiness：依赖可达才 200（PG + Valkey），不可达 503。部署闸门/流量入口用。
    依赖探测复用 health_monitor.collect（盲审 D：与 /metrics 同一口径，不另养第二套探测）。"""
    from src.health_monitor.collector import collect
    snap = collect()
    checks = {dep: ("ok" if ok else f"fail: {str(snap['deps'].get(f'{dep}_err', ''))[:60]}")
              for dep, ok in snap.get("deps", {}).items() if isinstance(ok, bool)}
    ok = all(v == "ok" for v in checks.values()) if checks else False
    if not ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    return {"status": "ok", "checks": checks}


@router.get("/metrics")
def metrics():
    """Prometheus 文本格式（text/plain; version=0.0.4）——业界交换标准。

    Zabbix HTTP agent（Prometheus pattern 预处理）/ Prometheus / Grafana 通吃。
    Phase 2 Zabbix 落地时在 nginx 层限源（只许 NAS Zabbix/内网）。
    """
    from src.health_monitor.collector import collect, render_prometheus
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(render_prometheus(collect()), media_type="text/plain; version=0.0.4; charset=utf-8")


# --- 健康监控（15 号 SM2：组件矩阵 + 事件流，admin）---

@router.get("/api/health/components")
def health_components_api(payload: dict = Depends(require_role("admin"))):
    """组件实时矩阵：collector 快照（systemd unit / 依赖 / hub 心跳 / 任务心跳）。

    与 /metrics 同源同口径（collector.collect），本端点给 Web 健康页用（带鉴权）。
    """
    from src.health_monitor.collector import collect
    return collect()


@router.get("/api/health/events")
def health_events_api(limit: int = 100, payload: dict = Depends(require_role("admin"))):
    """health_event 事件流（触发/恢复沿历史，30 天保留期，倒序）。"""
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT ts, rule_id, component, severity, detail FROM health_event "
            "ORDER BY ts DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    return {"events": [{"ts": str(r[0])[:19], "rule": r[1], "component": r[2],
                        "severity": r[3], "detail": r[4] or ""} for r in rows]}


# --- 系统配置（system_config，admin 可改，部分项支持动态生效） ---

def _adjust_celery_concurrency(new_value: int) -> dict:
    """动态调整 Celery worker 并发度（via broker 发 pool_grow/shrink 控制命令）。

    Web API 进程通过 Celery app 连同一个 Valkey broker，control 命令经 broker 推到 worker。
    """
    try:
        from src.scheduler.app import app as celery_app
        insp = celery_app.control.inspect()
        stats = insp.stats() or {}
        if not stats:
            return {"applied": False, "reason": "无 worker 在线（DB 已更新，下次 worker 启动生效）"}
        results = {}
        for worker_name, info in stats.items():
            current = info.get("pool", {}).get("max-concurrency", 2)
            delta = new_value - current
            if delta > 0:
                celery_app.control.pool_grow(delta, destination=[worker_name])
                results[worker_name] = f"{current} -> {new_value} (grow {delta})"
            elif delta < 0:
                celery_app.control.pool_shrink(-delta, destination=[worker_name])
                results[worker_name] = f"{current} -> {new_value} (shrink {-delta})"
            else:
                results[worker_name] = f"{current} (无变化)"
        return {"applied": True, "workers": results}
    except Exception as e:
        return {"applied": False, "reason": f"动态调整失败（DB 已更新，下次 worker 启动生效）: {e}"}


@router.get("/api/smtp-config")
def smtp_config_api(payload: dict = Depends(require_perm("user_mgmt"))):
    """邮件发信配置（整组读取；password 不回传明文，只回 password_set 标记）。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT key, value FROM system_config WHERE key LIKE 'smtp_%'")
        cfg = {k: v for k, v in cur.fetchall()}
    return {
        "host": cfg.get("smtp_host", ""),
        "port": cfg.get("smtp_port", "587"),
        "security": cfg.get("smtp_security", "auto"),
        "username": cfg.get("smtp_username", ""),
        "password_set": bool(cfg.get("smtp_password")),
        "from": cfg.get("smtp_from", ""),
    }


@router.post("/api/smtp-config")
def smtp_config_save_api(body: dict = Body(...),
                         payload: dict = Depends(require_perm("user_mgmt"))):
    """邮件发信配置整组保存。password 留空=保持不变；security ∈ auto/ssl/starttls。"""
    security = str(body.get("security", "auto")).strip() or "auto"
    if security not in ("auto", "ssl", "starttls"):
        raise ApiError(400, "SMTP_SECURITY_INVALID", "security 需为 auto / ssl / starttls")
    port = str(body.get("port", "587")).strip() or "587"
    try:
        int(port)
    except ValueError:
        raise ApiError(400, "SMTP_PORT_INVALID", "port 需为数字")
    from src.quant_common.crypto import encrypt
    values = {
        "smtp_host": str(body.get("host", "")).strip(),
        "smtp_port": port,
        "smtp_security": security,
        "smtp_username": str(body.get("username", "")).strip(),
        "smtp_from": str(body.get("from", "")).strip(),
    }
    with get_conn() as conn:
        for k, v in values.items():
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES (%s, %s, 'text', '') ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=now()",
                (k, v, v))
        pwd = str(body.get("password", "") or "").strip()
        if pwd:  # 留空=不变
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES ('smtp_password', %s, 'password', 'SMTP 密码（加密）') "
                "ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=now()",
                (encrypt(pwd), encrypt(pwd)))
        conn.commit()
    audit_log(payload["username"], "smtp_config_save",
              f"host={values['smtp_host']} port={port} security={security} pwd={'***' if pwd else 'unchanged'}")
    return {"ok": True}


@router.post("/api/email/test")
async def email_test_api(body: dict = Body(...), request: Request = None,
                         background_tasks: BackgroundTasks = None,
                         payload: dict = Depends(require_perm("user_mgmt"))):
    """发送测试邮件（走发件箱，立即可在 Logs 页看结果；失败自动指数退避重试）。"""
    to = str(body.get("to", "")).strip()
    if not to or "@" not in to:
        raise ApiError(400, "EMAIL_INVALID", "请填有效收件邮箱")
    subject = "测试邮件 · 人工智能开发学习平台"
    html = ("<html><body style='font-family:sans-serif'><h3>✅ 测试邮件</h3>"
            "<p>这是一封配置验证邮件。收到即表示 SMTP 发信配置正确。</p></body></html>")
    outbox_id = queue_email(to, subject, html)
    background_tasks.add_task(try_row, outbox_id)
    audit_log(payload["username"], "email_test", to)
    return {"queued": True, "outbox_id": outbox_id}


@router.get("/api/system-config")
def list_system_config(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """列系统配置（viewer+ 只读）。password 型不回传明文，返回空值 + has_value 标记。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT key, value, value_type, description, updated_at, updated_by "
            "FROM system_config ORDER BY key")
        rows = cur.fetchall()
    items = []
    for r in rows:
        value = r[1]
        if r[2] == "password":
            items.append({"key": r[0], "value": "", "has_value": bool(value),
                          "value_type": r[2], "description": r[3],
                          "updated_at": str(r[4]) if r[4] else None, "updated_by": r[5]})
        else:
            items.append({"key": r[0], "value": value, "value_type": r[2], "description": r[3],
                          "updated_at": str(r[4]) if r[4] else None, "updated_by": r[5]})
    return {"items": items}


@router.post("/api/system-config/{key}")
def update_system_config(key: str, body: dict = Body(...),
                          payload: dict = Depends(require_role("admin"))):
    """更新系统配置（仅 admin）。部分 key 支持动态生效（如 celery_concurrency）。
    password 型：留空=不修改（400 提示），非空=Fernet 加密存储。"""
    value = body.get("value")
    if value is None:
        raise ApiError(400, "CONFIG_VALUE_INVALID", "缺 value 字段")
    with get_conn() as conn:
        cur = conn.execute("SELECT value_type FROM system_config WHERE key=%s", (key,))
        row = cur.fetchone()
        if not row:
            raise ApiError(404, "CONFIG_KEY_NOT_FOUND", f"系统配置 {key} 不存在")
        value_type = row[0]
        # 类型校验 + 规范化
        if value_type == "int":
            try: value = str(int(value))
            except Exception: raise ApiError(400, "CONFIG_VALUE_INVALID", f"{key} 需 int 值")
        elif value_type == "float":
            try: value = str(float(value))
            except Exception: raise ApiError(400, "CONFIG_VALUE_INVALID", f"{key} 需 float 值")
        elif value_type == "bool":
            value = "true" if value in (True, "true", "True", "1", 1) else "false"
        elif value_type == "json":
            try: value = json.dumps(value) if not isinstance(value, str) else value
            except Exception: pass
        elif value_type == "password":
            value = str(value).strip()
            if not value:
                raise ApiError(400, "CONFIG_PASSWORD_EMPTY", "password 型留空=不修改；如需更换请填新值")
            from src.quant_common.crypto import encrypt
            value = encrypt(value)
        conn.execute(
            "UPDATE system_config SET value=%s, updated_at=now(), updated_by=%s WHERE key=%s",
            (str(value), payload["username"], key))
        conn.commit()
    audit_log(payload["username"], "update_system_config", key, "(password)" if value_type == "password" else str(value))

    # 动态生效：celery_concurrency 即时 pool_grow/shrink
    dynamic_result = None
    if key == "celery_concurrency":
        dynamic_result = _adjust_celery_concurrency(int(value))
    shown = "(password updated)" if value_type == "password" else value
    return {"key": key, "value": shown, "dynamic": dynamic_result}


@router.get("/api/system-config/{key}")
def get_system_config(key: str, payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """取单个系统配置。"""
    with get_conn() as conn:
        cur = conn.execute("SELECT value, value_type, description FROM system_config WHERE key=%s", (key,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "CONFIG_KEY_NOT_FOUND", f"系统配置 {key} 不存在")
    return {"key": key, "value": r[0], "value_type": r[1], "description": r[2]}


@router.get("/api/terms")
def terms_api():
    """平台使用条款（公开，注册页 + 开通邮件共用单一源）。
    返回 items: [{lang, name, body}] —— N 语言注册表驱动，前端遍历展示不感知具体语言。"""
    return {"items": get_terms_items()}


@router.get("/api/email-outbox")
def email_outbox_api(payload: dict = Depends(require_perm("user_mgmt"))):
    """发件箱状态（持久化 + 指数退避重发）：pending 重发中 / sent 已发 / failed 重试耗尽。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, to_email, subject, status, attempts, next_attempt_at, last_error, created_at, sent_at "
            "FROM email_outbox ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
    return {"items": [{
        "id": r[0], "to": r[1], "subject": r[2], "status": r[3], "attempts": r[4],
        "next_attempt_at": str(r[5])[:19] if r[5] else None,
        "last_error": r[6], "created_at": str(r[7])[:19], "sent_at": str(r[8])[:19] if r[8] else None,
    } for r in rows]}


@router.get("/api/notifications")
def notifications_api(status: str = "active", limit: int = 50,
                      payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """通知中心（站内铃铛/通知历史共用）。按当前角色过滤可见类别（email→admin 等）。"""
    from src.alert_notify import visible_categories
    cats = visible_categories(payload.get("role", "viewer"))
    if not cats:
        return {"items": [], "count": 0}
    cond = "" if status == "all" else "AND status=%s"
    params = [cats]
    if status != "all":
        params.append(status)
    params.append(min(limit, 200))
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, level, category, title, body, source_ref, status, created_at, acked_at, code "
            f"FROM notifications WHERE category = ANY(%s) {cond} "
            "ORDER BY id DESC LIMIT %s", tuple(params))
        rows = cur.fetchall()
        cur2 = conn.execute(
            "SELECT count(*) FROM notifications WHERE category = ANY(%s) AND status='active'",
            (cats,))
        active_count = cur2.fetchone()[0]
    return {
        "items": [{
            "id": r[0], "level": r[1], "category": r[2], "title": r[3], "body": r[4],
            "source_ref": r[5], "status": r[6],
            "created_at": str(r[7])[:19] if r[7] else "",
            "acked_at": str(r[8])[:19] if r[8] else None,
            "code": r[9],   # web 长尾批：结构化标识（runbook 映射键，未打码为 None）
        } for r in rows],
        "count": active_count,
    }


@router.post("/api/notifications/ack-all")
def notifications_ack_all(payload: dict = Depends(require_role("viewer", "analyst", "trader", "admin"))):
    """全部确认：当前角色可见类别的 active → acked。"""
    from src.alert_notify import visible_categories
    cats = visible_categories(payload.get("role", "viewer"))
    if not cats:
        return {"acked": 0}
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE notifications SET status='acked', acked_by=%s, acked_at=now() "
            "WHERE category = ANY(%s) AND status='active'",
            (payload.get("username", ""), cats))
        conn.commit()
    audit_log(payload["username"], "notifications_ack_all", f"n={cur.rowcount}")
    return {"acked": cur.rowcount}