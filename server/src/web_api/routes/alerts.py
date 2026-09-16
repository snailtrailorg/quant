"""Web 后端 · 告警订阅路由（批 7 起；批30 订阅用户化——docs/任务/批30-通知用户化与手机号.md）。

订阅 CRUD（维度=**用户**：被选用户的邮箱/手机/名下 bot 即投递通道，dispatch 展开裁量——
可用就发不可用跳过，软删用户 JOIN 过滤=自然出列表）+ 短信凭证端点（含批30 验证码模板键）
+ 用户维度测试。权限 alerts_config（admin 专属——告警路由/计费短信面不给 analyst）。
手机号回显打码（legacy 清单同走 _mask）。旧表 alert_channel_sub 保留不读写（legacy 只读展示）。
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
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")   # 批30：单源（auth_routes 手机号链 import 此处）


def _mask(channel: str, target: str | None) -> str | None:
    if not target:
        return target
    if channel == "sms" and len(target) == 11:
        return f"{target[:3]}****{target[-4:]}"
    return target


def _users_with_channels() -> list[dict]:
    """可订阅用户清单（enabled 未软删）+ 各自通道可用性。"""
    from src.alert_notify.sms import sms_configured
    sms_ok = sms_configured()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT u.id, u.username, u.nickname, u.email, u.phone, "
            "(SELECT count(*) FROM im_bot_config b WHERE b.owner_user_id=u.id AND b.enabled) "
            "FROM users u WHERE u.enabled AND u.deleted_at IS NULL ORDER BY u.id")
        return [{"id": r[0], "username": r[1], "nickname": r[2],
                 "channels": {"email": bool(r[3]), "sms": bool(r[4]) and sms_ok, "im": r[5] or 0}}
                for r in cur.fetchall()]


def _subs_from_db() -> list[dict]:
    """软删/停用用户行不入列（A-P2-2 裁定=用户裁定"删用户自然从通知列表删除"——管理面与
    投递面同口径；孤儿行 inert，随旧表清理批扫除）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.user_id, s.categories, s.min_level, s.enabled, u.username, u.nickname "
            "FROM alert_user_sub s JOIN users u ON u.id=s.user_id "
            "AND u.enabled AND u.deleted_at IS NULL ORDER BY s.id")
        return [{"id": r[0], "user_id": r[1], "categories": r[2] or [], "min_level": r[3],
                 "enabled": r[4], "username": r[5], "nickname": r[6]} for r in cur.fetchall()]


def _legacy_from_db() -> list[dict]:
    """旧通道订阅清单（只读提示——**仅 email/sms 行**（A-P2-6：im 行已自动迁移，列入=误导
    admin 多余重建）；批30 §三过渡语义）。"""
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT channel, target FROM alert_channel_sub WHERE channel IN ('email','sms') ORDER BY id")
            return [{"channel": r[0], "target": _mask(r[0], r[1])} for r in cur.fetchall()]
    except Exception:
        return []


@router.get("/api/alerts/config")
def alerts_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """订阅列表（批30 用户维度）+ 可订阅用户清单 + 旧表遗留行。"""
    from src.alert_notify.sms import sms_configured
    from src.alert_notify.dispatch import _LIMITS
    users = _users_with_channels()
    chans = {u["id"]: u["channels"] for u in users}
    subs = []
    for s in _subs_from_db():
        s = dict(s)
        s["channels"] = chans.get(s["user_id"], {"email": False, "sms": False, "im": 0})
        subs.append(s)
    return {"subs": subs, "users": users, "sms_configured": sms_configured(),
            "legacy": _legacy_from_db(), "quota": dict(_LIMITS)}


def _validate_sub_body(body: dict, *, partial: dict | None = None) -> tuple[list, str, bool]:
    base = partial or {}
    cats = body.get("categories", base.get("categories", []))
    if not isinstance(cats, list) or any(c not in _CATEGORIES for c in cats):
        raise ApiError(400, "BAD_REQUEST", "categories 须为 risk/task/data/system 子集")
    min_level = body.get("min_level", base.get("min_level", "warn"))
    if min_level not in ("warn", "critical"):
        raise ApiError(400, "BAD_REQUEST", "min_level 须为 warn|critical")
    enabled = bool(body.get("enabled", base.get("enabled", True)))
    return cats, min_level, enabled


@router.post("/api/alerts/config")
def alerts_config_create(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """新增订阅行（批30：维度=用户）。全表上限 10 行=最多 10 个订阅用户。"""
    try:
        uid = int(body.get("user_id") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", "user_id 须为数字")
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT username FROM users WHERE id=%s AND enabled AND deleted_at IS NULL", (uid,))
        u = cur.fetchone()
        if not u:
            raise ApiError(400, "BAD_REQUEST", "用户不存在或不可订阅（停用/软删）")
        cur = conn.execute("SELECT count(*) FROM alert_user_sub")
        if cur.fetchone()[0] >= 10:   # 补审C 行数上限保形（per-channel 概念消失=全表 10 用户）
            raise ApiError(400, "BAD_REQUEST", "订阅用户至多 10 个")
    cats, min_level, enabled = _validate_sub_body(body)
    import json as _json
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO alert_user_sub (user_id, categories, min_level, enabled) "
                "VALUES (%s, %s::jsonb, %s, %s) RETURNING id",
                (uid, _json.dumps(cats), min_level, enabled))
            new_id = cur.fetchone()[0]
            conn.commit()
        except Exception as e:
            conn.rollback()
            if type(e).__name__ == "UniqueViolation":
                raise ApiError(409, "DUPLICATE_SUB", "该用户已订阅")
            raise
    audit_log(payload["username"], "alerts_config_create",
              detail=f"#{new_id} user={u[0]} cats={cats} level={min_level} enabled={enabled}")
    return {"id": new_id}


def _load_row(row_id: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT s.id, s.user_id, s.categories, s.min_level, s.enabled, u.username "
            "FROM alert_user_sub s JOIN users u ON u.id=s.user_id "
            "AND u.enabled AND u.deleted_at IS NULL WHERE s.id=%s", (row_id,))   # A-P2-7：软删用户行不可操作（含 test 面不再对死号发真实短信）
        r = cur.fetchone()
    if not r:
        raise ApiError(404, "ROW_NOT_FOUND", f"订阅行 {row_id} 不存在")
    return {"id": r[0], "user_id": r[1], "categories": r[2] or [], "min_level": r[3],
            "enabled": r[4], "username": r[5]}


@router.put("/api/alerts/config/{row_id}")
def alerts_config_update(row_id: int, body: dict = Body(...),
                         payload: dict = Depends(require_perm("alerts_config"))):
    """改订阅行（批30：用户不可改——换人=删了重建；类别/级别/开关可改）。"""
    row = _load_row(row_id)
    if "user_id" in body:
        try:
            new_uid = int(body.get("user_id") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", "user_id 须为数字")
        if new_uid != row["user_id"]:
            raise ApiError(400, "BAD_REQUEST", "订阅用户不可改（删除后重新添加）")
    cats, min_level, enabled = _validate_sub_body(body, partial=row)
    import json as _json
    with get_conn() as conn:
        conn.execute(
            "UPDATE alert_user_sub SET categories=%s::jsonb, min_level=%s, enabled=%s, "
            "updated_at=now() WHERE id=%s",
            (_json.dumps(cats), min_level, enabled, row_id))
        conn.commit()
    audit_log(payload["username"], "alerts_config_update",
              detail=f"#{row_id} user={row['username']} cats={cats} level={min_level} enabled={enabled}")


@router.delete("/api/alerts/config/{row_id}")
def alerts_config_delete(row_id: int, payload: dict = Depends(require_perm("alerts_config"))):
    row = _load_row(row_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM alert_user_sub WHERE id=%s", (row_id,))
        conn.commit()
    audit_log(payload["username"], "alerts_config_delete", detail=f"#{row_id} user={row['username']}")
    return {"ok": True}


@router.get("/api/alerts/sms-config")
def sms_config_get(payload: dict = Depends(require_perm("alerts_config"))):
    """凭证状态：只回 secret_set 布尔与非密钥项（access_key_id 不回显）。"""
    from src.alert_notify.sms import _sms_config, sms_configured
    cfg = _sms_config() or {}
    # verify 模板键独立读（不入 _CFG_KEYS——A-P1-5：未配验证码模板不应影响告警面判定）
    verify_tpl = ""
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT value FROM system_config WHERE key='alert_sms_verify_template_code'").fetchone()
        verify_tpl = str(r[0]).strip() if r and r[0] else ""
    except Exception:
        pass
    return {"secret_set": bool(cfg.get("alert_sms_access_key_secret")),
            "sms_configured": sms_configured(),
            "sign_name": cfg.get("alert_sms_sign_name", ""),
            "template_code": cfg.get("alert_sms_template_code", ""),
            "verify_template_code": verify_tpl}


@router.put("/api/alerts/sms-config")
def sms_config_put(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    from src.quant_common.crypto import encrypt
    fields = {"access_key_id": "alert_sms_access_key_id",
              "access_key_secret": "alert_sms_access_key_secret",
              "sign_name": "alert_sms_sign_name",
              "template_code": "alert_sms_template_code",
              "verify_template_code": "alert_sms_verify_template_code"}   # 批30：验证码模板（明文 text）
    with get_conn() as conn:
        for k, col in fields.items():
            v = (body.get(k) or "").strip()
            if not v:
                continue   # 留空=不修改（smtp 先例）
            if k == "access_key_secret":
                v = encrypt(v)
            conn.execute(
                "INSERT INTO system_config (key, value, value_type, description) "
                f"VALUES ('{col}', %s, {'%r' % ('password' if k == 'access_key_secret' else 'text')}, '阿里云短信凭证（批7/批30）') "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (v,))
        conn.commit()
    audit_log(payload["username"], "alerts_sms_config_save",
              detail=f"keys={[k for k in fields if (body.get(k) or '').strip()]}")
    return {"ok": True}


@router.post("/api/alerts/test")
def alerts_test(body: dict = Body(...), payload: dict = Depends(require_perm("alerts_config"))):
    """用户维度测试发送（批30）：按订阅行用户的三通道各试一遍（绕节流/配额）；per-actor 60s
    冷却；结果落 code=alert.test 站内通知。ok=已试通道全过（零可用通道=False）。"""
    try:
        _rid = int(body.get("id") or 0)
    except (TypeError, ValueError):
        raise ApiError(400, "BAD_REQUEST", "id 须为数字")
    row = _load_row(_rid)
    actor = payload["username"]
    r = get_redis()
    cool = f"alert:test:cooldown:{actor}:{row['id']}"
    if not r.set(cool, "1", nx=True, ex=60):
        raise ApiError(429, "TOO_MANY_REQUESTS", "测试冷却中（60s/订阅行）")
    with get_conn() as conn:
        cur = conn.execute("SELECT email, phone FROM users WHERE id=%s", (row["user_id"],))
        u = cur.fetchone() or ("", "")
        cur = conn.execute("SELECT id FROM im_bot_config WHERE owner_user_id=%s AND enabled ORDER BY id",
                           (row["user_id"],))
        bots = [b[0] for b in cur.fetchall()]
    results: list[tuple[bool, str]] = []

    # —— 邮箱（有值即试——outbox 60s 兜，发送记录页可查）——
    if u[0]:
        try:
            from src.email_service import queue_email
            queue_email(u[0], "[test] 告警通道测试", "<pre>这是一封测试邮件（设置→告警→测试）</pre>")
            results.append((True, "邮件已入发送队列"))
        except Exception:
            results.append((False, "邮件入队失败: smtp_error"))

    # —— 短信（有手机且凭证齐）——
    if u[1]:
        from src.alert_notify.sms import send_sms, sms_configured
        if not sms_configured():
            results.append((False, "短信未接入（凭证未配）"))
        else:
            ok, reason = send_sms(u[1], "info", "告警通道测试")
            results.append((ok, "短信已发送" if ok else f"短信失败: {reason}"))

    # —— IM（名下每个 enabled bot——沿用批 8.5 真发语义：test_connection + 发绑定用户）——
    for bid in bots:
        from src.im_bot.base import get_im_provider
        with get_conn() as conn:
            cur = conn.execute("SELECT provider FROM im_bot_config WHERE id=%s AND enabled", (bid,))
            b = cur.fetchone()
            cur = conn.execute("SELECT count(*), array_agg(im_user_id) FROM im_bot_users WHERE bot_id=%s",
                               (bid,))
            agg = cur.fetchone()
        n_bound, bound = (agg[0] or 0), (agg[1] or [])
        if not b:
            results.append((False, f"机器人 {bid} 不存在或未启用"))
            continue
        p = get_im_provider(b[0])
        if not p:
            results.append((False, f"机器人 {bid} provider 缺失"))
            continue
        ok, detail = p.test_connection(bid)
        if not ok:
            results.append((False, f"机器人 {bid} 连接失败: {str(detail)[:40]}"))
            continue
        if n_bound == 0:
            results.append((False, f"机器人 {bid} 无绑定用户（先对它发条消息完成绑定）"))
            continue
        sent = sum(1 for uu in bound
                   if p.send_text(bid, uu, "open_id", "[test] 告警通道测试——收到本条即 IM 告警链路已通"))
        results.append((sent > 0, f"机器人 {bid} 连接正常，测试消息已发 {sent}/{n_bound} 位绑定用户"))

    if not results:
        ok, detail = False, "该用户没有可用通道（未设邮箱/手机，且名下无启用机器人）"
    else:
        ok = all(r0 for r0, _ in results)
        detail = "；".join(d for _, d in results)
    try:
        from src.alert_notify.notify import notify
        notify("info", "system", f"告警测试[user] · {'成功' if ok else '失败'}",
              f"{detail}（by {actor}）", code="alert.test")
    except Exception as e:
        import logging
        logging.getLogger("web_api").warning("alert.test notify failed: %s", e)
    audit_log(payload["username"], "alerts_test", detail=f"user={row['username']} ok={ok} {detail[:120]}")
    return {"ok": ok, "detail": detail}
