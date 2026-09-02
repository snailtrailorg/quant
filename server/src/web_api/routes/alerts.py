"""Web 后端 · 告警订阅路由（批 7 · /api/alerts/*，docs/任务/批7-告警订阅分发.md）。

订阅 CRUD（批7.1 多目标：每通道可多行——多邮箱/多手机/多 bot；统一列表+行级增删改）
+ 短信凭证专用端点 + 行级测试。权限 alerts_config（admin 专属——告警路由/计费短信面不给
analyst）。手机号回显打码；target 含 * = 保留库值（打码 sentinel，smtp password 先例）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Body

from ..auth import require_perm, audit_log
from ..errors import ApiError
from ..redis_pool import redis_client as get_redis
from src.data_platform.db import get_conn

router = APIRouter(tags=["alerts"])

_CATEGORIES = ("risk", "task", "data", "system")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def _mask(channel: str, target: str | None) -> str | None:
    if not target:
        return target
    if channel == "sms" and len(target) == 11:
        return f"{target[:3]}****{target[-4:]}"
    return target


def _channels_from_db(only_enabled: bool = False) -> list[dict]:
    sql = ("SELECT id, channel, target, categories, min_level, enabled FROM alert_channel_sub "
           + ("WHERE enabled" if only_enabled else "") + " ORDER BY id")
    with get_conn() as conn:
        cur = conn.execute(sql)
        return [{"id": r[0], "channel": r[1], "target": r[2], "categories": r[3] or [],
                 "min_level": r[4], "enabled": r[5]} for r in cur.fetchall()]


def _validate_target(ch: str, target: str, bot_ids: set[int]) -> None:
    """类型驱动校验（批7.1：IM=bot id / email=正则 / sms=大陆手机号）。"""
    if ch == "email":
        if not _EMAIL_RE.match(target):
            raise ApiError(400, "BAD_REQUEST", "邮箱格式不合法")
    elif ch == "sms":
        if not _PHONE_RE.match(target):
            raise ApiError(400, "BAD_REQUEST", "手机号格式不合法（仅支持中国大陆）")
    elif ch == "im":
        try:
            bid = int(target)
        except ValueError:
            raise ApiError(400, "BAD_REQUEST", "IM 目标须为 bot id")
        if bid not in bot_ids:
            raise ApiError(400, "BAD_REQUEST", "IM 目标须为存在且启用的 bot")
    else:
        raise ApiError(400, "BAD_REQUEST", f"非法通道: {ch}")


@router.get("/api/alerts/config")
def alerts_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """订阅列表（批7.1 多目标：统一列表+行级 CRUD，不再每通道单行）。"""
    from src.alert_notify.sms import sms_configured
    from src.alert_notify.dispatch import _LIMITS
    out = []
    for r in _channels_from_db():
        r = dict(r)
        r["target"] = _mask(r["channel"], r["target"])
        out.append(r)
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT b.id, b.provider, b.name, b.enabled, "
            "(SELECT count(*) FROM im_bot_users u WHERE u.bot_id = b.id) AS n "
            "FROM im_bot_config b ORDER BY b.id")
        bots = [{"id": r[0], "provider": r[1], "name": r[2], "enabled": r[3],
                 "bound_users": r[4]} for r in cur.fetchall()]
    return {"channels": out, "sms_configured": sms_configured(), "im_bots": bots,
            "quota": dict(_LIMITS)}


@router.post("/api/alerts/config")
def alerts_config_create(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """新增订阅行（批7.1 多目标：统一列表+行级 CRUD）。"""
    ch = body.get("channel")
    target = (body.get("target") or "").strip()
    cats = body.get("categories", [])
    if not isinstance(cats, list) or any(c not in _CATEGORIES for c in cats):
        raise ApiError(400, "BAD_REQUEST", "categories 须为 risk/task/data/system 子集")
    min_level = body.get("min_level", "critical" if ch == "sms" else "warn")
    if min_level not in ("warn", "critical"):
        raise ApiError(400, "BAD_REQUEST", "min_level 须为 warn|critical")
    enabled = bool(body.get("enabled"))
    if enabled and not target:
        raise ApiError(400, "TARGET_REQUIRED", "启用订阅须填目标")
    import json as _json
    with get_conn() as conn:
        # 补审D-P1：门控恢复旧契约——启用且非空才验目标（禁用草稿/所引 bot 已下线的行可关可存）
        if enabled and target:
            cur = conn.execute("SELECT id FROM im_bot_config WHERE enabled")
            _validate_target(ch, target, {r[0] for r in cur.fetchall()})
        cur = conn.execute("SELECT count(*) FROM alert_channel_sub WHERE channel=%s", (ch,))
        if cur.fetchone()[0] >= 10:   # 补审C：行数上限（扇出/配额 INCR/同行 UPDATE 无界）
            raise ApiError(400, "BAD_REQUEST", "该通道订阅至多 10 行")
        try:
            cur = conn.execute(
                "INSERT INTO alert_channel_sub (channel, target, categories, min_level, enabled) "
                "VALUES (%s, %s, %s::jsonb, %s, %s) RETURNING id",
                (ch, target or None, _json.dumps(cats), min_level, enabled))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            if type(e).__name__ == "UniqueViolation":
                raise ApiError(409, "DUPLICATE_SUB", "该订阅（通道+目标）已存在")
            raise
    audit_log(payload["username"], "alerts_config_create",
              detail=f"#{new_id} {ch} {_mask(ch, target)}")
    return {"id": new_id}


def _load_row(row_id: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, channel, target, categories, min_level, enabled "
            "FROM alert_channel_sub WHERE id=%s", (row_id,))
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "ROW_NOT_FOUND", f"订阅行 {row_id} 不存在")
    return {"id": r[0], "channel": r[1], "target": r[2], "categories": r[3] or [],
            "min_level": r[4], "enabled": r[5]}


@router.put("/api/alerts/config/{row_id}")
def alerts_config_update(row_id: int, body: dict = Body(...),
                         payload: dict = Depends(require_perm("alerts_config"))):
    """改订阅行。target 含 * = 保留库值（打码 sentinel，smtp password 先例）。类型不可改（IM/email/sms
    目标语义不同，改类型=删了重建）。"""
    row = _load_row(row_id)
    ch = row["channel"]
    target = (body.get("target") if body.get("target") is not None else row["target"]) or ""
    target = target.strip()
    if "*" in target and row["target"]:
        target = row["target"]           # sentinel：保持库值
    cats = body.get("categories", row["categories"])
    if not isinstance(cats, list) or any(c not in _CATEGORIES for c in cats):
        raise ApiError(400, "BAD_REQUEST", "categories 须为 risk/task/data/system 子集")
    min_level = body.get("min_level", row["min_level"])
    if min_level not in ("warn", "critical"):
        raise ApiError(400, "BAD_REQUEST", "min_level 须为 warn|critical")
    enabled = bool(body.get("enabled", row["enabled"]))
    if enabled and not target:
        raise ApiError(400, "TARGET_REQUIRED", "启用订阅须填目标")
    import json as _json
    with get_conn() as conn:
        if enabled and target:   # 补审D-P1：门控同 POST
            cur = conn.execute("SELECT id FROM im_bot_config WHERE enabled")
            _validate_target(ch, target, {r[0] for r in cur.fetchall()})
        try:
            conn.execute(
                "UPDATE alert_channel_sub SET target=%s, categories=%s::jsonb, "
                "min_level=%s, enabled=%s, updated_at=now() WHERE id=%s",
                (target or None, _json.dumps(cats), min_level, enabled, row_id))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if type(e).__name__ == "UniqueViolation":
                raise ApiError(409, "DUPLICATE_SUB", "该订阅（通道+目标）已存在")
            raise
    audit_log(payload["username"], "alerts_config_update",
              detail=f"#{row_id} {ch} {item_desc(ch, target, enabled)}")


def item_desc(ch, target, enabled):
    return f"enabled={enabled}, target={_mask(ch, target) if target else '(空)'}"


@router.delete("/api/alerts/config/{row_id}")
def alerts_config_delete(row_id: int, payload: dict = Depends(require_perm("alerts_config"))):
    row = _load_row(row_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM alert_channel_sub WHERE id=%s", (row_id,))
        conn.commit()
    audit_log(payload["username"], "alerts_config_delete",
              detail=f"#{row_id} {row['channel']} {_mask(row['channel'], row['target'] or '')}")
    return {"ok": True}


@router.get("/api/alerts/sms-config")
def sms_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """凭证状态：只回 secret_set 布尔与非密钥项（access_key_id 不回显）。"""
    from src.alert_notify.sms import _sms_config, sms_configured
    cfg = _sms_config() or {}
    return {"secret_set": bool(cfg.get("alert_sms_access_key_secret")),
            "sms_configured": sms_configured(),
            "sign_name": cfg.get("alert_sms_sign_name", ""),
            "template_code": cfg.get("alert_sms_template_code", "")}


@router.put("/api/alerts/sms-config")
def sms_config_put(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    from src.quant_common.crypto import encrypt
    fields = {"access_key_id": "alert_sms_access_key_id",
              "access_key_secret": "alert_sms_access_key_secret",
              "sign_name": "alert_sms_sign_name",
              "template_code": "alert_sms_template_code"}
    with get_conn() as conn:
        for k, col in fields.items():
            v = (body.get(k) or "").strip()
            if not v:
                continue   # 留空=不修改（smtp 先例）
            if k == "access_key_secret":
                v = encrypt(v)
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                f"VALUES ('{col}', %s, {'%r' % ('password' if k == 'access_key_secret' else 'text')}, '阿里云短信凭证（批7）') "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (v,))
        conn.commit()
    audit_log(payload["username"], "alerts_sms_config_save",
              detail=f"keys={[k for k in fields if (body.get(k) or '').strip()]}")
    return {"ok": True}


@router.post("/api/alerts/test")
def alerts_test(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """逐通道测试发送（绕节流/配额）；per-actor 60s 冷却；结果落 code=alert.test 站内通知。"""
    try:
        _rid = int(body.get("id") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", "id 须为数字")
    row = _load_row(_rid)
    ch = row["channel"]
    actor = payload["username"]
    r = get_redis()
    cool = f"alert:test:cooldown:{actor}:{row['id']}"
    if not r.set(cool, "1", nx=True, ex=60):   # 原子冷却（批7.1 行级）
        raise ApiError(429, "TOO_MANY_REQUESTS", "测试冷却中（60s/订阅行）")
    detail, ok = "", False
    if ch == "im":
        from src.im_bot.base import get_im_provider
        if not row or not row.get("target"):
            detail, ok = "通道未配置", False
        else:
            with get_conn() as conn:
                cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled",
                                   (int(row["target"]),))
                b = cur.fetchone()
                cur = conn.execute("SELECT count(*) FROM im_bot_users WHERE bot_id=%s",
                                   (int(row["target"]),))
                n_bound = cur.fetchone()[0]
            if not b:
                detail, ok = "bot 不存在或未启用", False
            elif n_bound == 0:
                detail, ok = "无绑定用户（先在飞书对 bot 发送任意消息完成绑定）", False
            else:
                # 批 8.5（2026-09-02）：send_text 已真实 bool（批 7 改造），测试升级为
                # test_connection + 真发一条到全体绑定用户（email/sms 同款真发语义，e2e 闭环）
                p = get_im_provider(b[0])
                if not p:
                    ok, detail = False, "provider 缺失"
                else:
                    ok, detail = p.test_connection(int(row["target"]))
                    if ok:
                        with get_conn() as conn:
                            cur = conn.execute("SELECT im_user_id FROM im_bot_users WHERE bot_id=%s",
                                               (int(row["target"]),))
                            bound = [r[0] for r in cur.fetchall()]
                        sent = sum(1 for u in bound
                                   if p.send_text(int(row["target"]), u, "open_id", "[test] 告警通道测试——收到本条即 IM 告警链路已通"))
                        detail = f"连接正常，测试消息已发 {sent}/{len(bound)} 位绑定用户" if sent else "连接正常但 0 人送达（查绑定/发送日志）"
                        ok = sent > 0
                    else:
                        detail = str(detail)[:80] or "连接失败"
    elif ch == "email":
        # B 评 P3：只入队不同步试发——SMTP 60s > axios 30s，同步会让前端超时；
        # 发送由 beat sweep（60s 周期）兜，outbox 页可查（测试通知 code=alert.test 同步可见）
        from src.email_service import queue_email
        if not row or not row.get("target"):
            detail, ok = "通道未配置", False
        else:
            try:
                queue_email(row["target"], "[test] 告警通道测试",
                            "<pre>这是一封测试邮件（设置→告警→测试）</pre>")
                ok, detail = True, "已入发送队列（outbox 60s 内发出，发送记录页可查）"
            except Exception:
                ok, detail = False, "发送失败: smtp_error"
    else:
        from src.alert_notify.sms import send_sms, sms_configured
        if not sms_configured():
            detail, ok = "短信未接入（API key 未申请，凭证到位即通）", False
        elif not row or not row.get("target"):
            detail, ok = "通道未配置手机号", False
        else:
            ok, reason = send_sms(row["target"], "info", "告警通道测试")
            detail = "已发送" if ok else f"发送失败: {reason}"

    try:
        from src.alert_notify.notify import notify
        notify("info", "system", f"告警测试[{ch}] · {'成功' if ok else '失败'}",
              f"{detail}（by {actor}）", code="alert.test")
    except Exception as e:
        import logging
        logging.getLogger("web_api").warning("alert.test notify failed: %s", e)
    audit_log(payload["username"], "alerts_test", detail=f"channel={ch} ok={ok}")   # A 评 P2-4:计费面必留痕
    return {"ok": ok, "detail": detail}
