"""飞书扫码接入 onboarding 核心（批12A 去 celery 化重构）。

v2 双盲审关键修正（A/B 同判 P0）：confirming 触发点=register_app **返回之后**——SDK 实证
on_status_change 只发 domain_switched/polling/slow_down，"手机已确认"信号不存在（确认与
拿凭证同一瞬间：下一次 poll 直接带回 client_id/secret）。

线程模型（批12A）：web-api 进程内 daemon 线程跑 run_onboarding（SDK requests 无 timeout →
cancel_event+deadline 看门狗+模块级 Semaphore(8) 全局帽——A-P1-1）；出码经 threading.Event
由端点同步等待返回（≤5s），确认段继续写 session 供轮询/批B SSE。
"""
from __future__ import annotations
import os
import json
import logging
import re
import threading
import time
import redis
import lark_oapi as lark

from src.data_platform.db import get_conn
from src.quant_common.crypto import encrypt

logger = logging.getLogger("feishu_bot")
VALKEY_URL = os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4")
_redis = redis.Redis.from_url(VALKEY_URL, decode_responses=True)


def _set_session(session_id: str, data: dict, expire: int = 600,
                 owner_user_id: int | None = None) -> None:
    """存扫码会话状态。批12A：owner 显式传参（v1 的 _SESSION_OWNER 全局 dict 慢泄漏退役——B-P2-4）；
    载荷带 ts（wall clock——B-P2-6 批 B SSE 去重钩子；非单调,回拨可倒退,去重勿依赖严格递增）。"""
    data = {**data, "owner_user_id": owner_user_id, "ts": time.time()}
    _redis.setex(f"feishu:session:{session_id}", expire, json.dumps(data, ensure_ascii=False))


# 批12A（A-P1-1②）：全局并发帽——SDK requests 无 timeout，daemon 线程理论可挂到 deadline；
# 信号量把"频控限全局"从口号变机制（每用户 1 活 ticket 之外的总闸）
_ONBOARD_SEMAPHORE = threading.BoundedSemaphore(8)


def acquire_onboarding_slot() -> bool:
    return _ONBOARD_SEMAPHORE.acquire(blocking=False)


def release_onboarding_slot() -> None:
    try:
        _ONBOARD_SEMAPHORE.release()
    except ValueError:
        pass


# 批12A（#5）：应用名后缀——sanitize（SDK app_preset 仅预填手机创建页，真名来自 bot/v3/info）
def _app_suffix(username: str | None) -> str:
    if not username or not isinstance(username, str):
        return "用户"
    s = re.sub(r"[^\w-]", "", username)[:16]
    return s if s else "用户"


def run_onboarding(session_id: str, owner_user_id: int | None = None,
                   on_qr=None, on_error=None, deadline_s: float = 720.0):
    """扫码接入核心（web 进程线程内跑；批12A 去 celery）。

    owner_user_id：自助=会话用户；admin=None（平台级）。
    重扫三分支（批11D）：他人=error / 平台=只刷凭证 / 自己或 admin=现状。
    on_qr(info)：出码回调（端点经 Event 同步等它——快失败走 on_error 短路）。
    deadline_s：cancel_event+看门狗（SDK requests 无 timeout——"≤600s"承诺的机制，A-P1-1）。
    """
    cancel = threading.Event()
    _watchdog = threading.Timer(deadline_s, cancel.set)   # 到点置 cancel——SDK poll 间检查即退出
    _watchdog.daemon = True
    _watchdog.start()
    # 批12A #5：自助名带 sanitize 后缀（防同名混淆链——22 号 §3.3-1 根因）；admin 保持原名
    _name = "量化交易助手" if owner_user_id is None else f"量化-{_app_suffix(_current_username(owner_user_id))}"
    app_preset = {
        "name": _name,
        "desc": "多市场量化交易平台飞书机器人",
    }

    def on_qr_code(info):
        # info 含 url（二维码内容）+ expire_in；生成 base64 二维码图片供前端渲染
        import qrcode, io, base64
        url = info.get("url", "")
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        _set_session(session_id, {
            "status": "scanning",
            "qr_url": url,
            "qr_img": qr_b64,
            "expire_in": info.get("expire_in", 600),
        }, expire=info.get("expire_in", 600), owner_user_id=owner_user_id)
        if on_qr:
            on_qr({"qr_url": url, "qr_img": qr_b64, "expire_in": info.get("expire_in", 600)})

    # pending 由端点先写（批12A TOCTOU 收口）——此处不覆写（admin 直调/旧路径兜底）
    if not _redis.exists(f"feishu:session:{session_id}"):
        _set_session(session_id, {"status": "pending"}, expire=900, owner_user_id=owner_user_id)

    # 默认权限（addons）：发消息 + 收消息事件 + 卡片回调
    addons = {
        "scopes": {"tenant": ["im:message:send_as_bot"]},
        "events": {"items": {"tenant": ["im.message.receive_v1"]}},
        "callbacks": {"items": ["card.action.trigger"]},
    }
    try:
        result = lark.register_app(
            on_qr_code=on_qr_code,
            on_status_change=lambda info: logger.info(f"feishu register status: {info}"),
            app_preset=app_preset,
            addons=addons,
            cancel_event=cancel,   # 批12A：SDK 支持——poll 间检查，看门狗到点即退出（A-P1-1）
        )
        _watchdog.cancel()
        # 批12A #2（v2 修正，A/B 同判 P0）：confirming=register_app 返回后（SDK 无"手机已确认"
        # 前置信号——确认与拿凭证同一瞬间）；窗口=httpx 取名+落库+发码（1~3s），文案诚实对应
        _set_session(session_id, {"status": "confirming"}, expire=120, owner_user_id=owner_user_id)
        # 成功：result 含 client_id/client_secret（SDK 返回 dict 或对象，兼容两种）
        if hasattr(result, "get"):
            app_id = result.get("client_id", "")
            app_secret = result.get("client_secret", "")
        else:
            app_id = getattr(result, "client_id", "")
            app_secret = getattr(result, "client_secret", "")

        if not app_id:
            _set_session(session_id, {"status": "error", "error": "register_app 未返回 app_id"},
                         owner_user_id=owner_user_id)   # 盲审 A-P1-1：显式传参改造漏点（归属守卫失效）
            return

        # 获取应用名称（调飞书 API）
        import httpx
        app_name = ""
        try:
            token_resp = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
            token = token_resp.json().get("tenant_access_token", "")
            if token:
                # /bot/v3/info 获取机器人信息（机器人自己查自己，不需应用管理权限）
                app_resp = httpx.get("https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"}, timeout=10)
                bot_data = app_resp.json()
                logger.info(f"bot/v3/info 返回: {bot_data}")
                app_name = bot_data.get("bot", {}).get("app_name", "")
        except Exception as e:
            logger.warning(f"获取飞书应用名称失败: {e}")

        # 批 2(arch-19 v2):存 im_bot_config 统一表——同 app_id 重扫=更新凭证(ON CONFLICT
        # route_key),不再堆重复行(修批 1 审计 A-S1 揭示的旧行为)
        import json as _json
        from src.im_bot.credentials import save_bot_credentials, get_bot_credentials
        def _rescan_verdict(row_owner) -> str:
            """重扫三分支判定（批11D 盲审 A-P1-1+B-P1-1）→ 'error'|'platform'|'own'。"""
            if row_owner is not None and owner_user_id is not None and row_owner != owner_user_id:
                return "error"
            if row_owner is None and owner_user_id is not None:
                return "platform"
            return "own"

        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, credentials_encrypted, owner_user_id FROM im_bot_config "
                "WHERE provider='feishu' AND params->>'route_key'=%s", (app_id,))
            row = cur.fetchone()
            rescan = None
            ins = None
            if not row:
                creds = {"app_id": app_id, "app_secret": app_secret}
                ins = conn.execute(
                    "INSERT INTO im_bot_config (provider, name, default_role, enabled, "
                    "owner_user_id, credentials_encrypted, params) "
                    "VALUES ('feishu', %s, 'viewer', true, %s, %s, %s::jsonb) "
                    "ON CONFLICT (provider, (params->>'route_key')) DO NOTHING "
                    "RETURNING id",
                    (app_name, owner_user_id,
                     encrypt(_json.dumps(creds, ensure_ascii=False)),
                     _json.dumps({"route_key": app_id}))).fetchone()
                conn.commit()
            bind_code = None   # 批11E：仅 ins 真新建且自助 owner 发码（重扫/平台级/admin 不发——B-P1-1/A-P2）
            if ins:
                _audit(owner_user_id, "im_register_create", ins[0], app_id)   # 真插入=新 bot（盲审 P2-1：避开重扫分支误标）
                if owner_user_id is not None:
                    from src.im_bot.bindcode import gen_code, issue
                    bind_code = gen_code()
                    try:
                        issue(ins[0], bind_code)      # 双写之一（键）；session 载荷在末尾 done 处——同码
                    except Exception as e:
                        bind_code = None
                        logger.warning(f"验证码写入失败(不影响接入): {e}")
            elif not row:
                # A-P2-6：并发同 app 冲突（INSERT 被吞）→ 重查行走进重扫判定
                cur = conn.execute(
                    "SELECT id, credentials_encrypted, owner_user_id FROM im_bot_config "
                    "WHERE provider='feishu' AND params->>'route_key'=%s", (app_id,))
                row = cur.fetchone()
            if row:
                rescan = _rescan_verdict(row[2])

            if row and rescan == "error":
                # 他人 bot：拒合并（凭证/归属/enabled 全不动）——扫描者明确失败而非静默死胡同
                _set_session(session_id, {"status": "error",
                                          "error": "该应用已被其他账号接入", "code": "ONBOARDING_APP_TAKEN"},
                             expire=600, owner_user_id=owner_user_id)
                logger.warning(f"扫码重扫拒: app_id={app_id} 已归属 user={row[2]} 本次 owner={owner_user_id}")
                return

            if row and rescan == "platform":
                # 平台级 bot：只刷新凭证（扫码者持有该 app 飞书侧管理权）——不翻 enabled 不改名
                save_bot_credentials(row[0], {"app_id": app_id, "app_secret": app_secret})
                conn.commit()
                from src.im_bot.feishu_client import evict_feishu_client
                evict_feishu_client(row[0])
                _set_session(session_id, {"status": "done", "app_id": app_id, "owned": False, "note": "platform_bot"},
                             expire=600, owner_user_id=owner_user_id)
                logger.info(f"扫码重扫平台级 bot #{row[0]}：仅刷新凭证（owner 保持 NULL）")
                _audit(owner_user_id, "owner_im_rescan_platform", row[0], app_id)
                return

            if row:
                # 自己/admin 重扫：合并凭证(保已有 token/ek)+翻 enabled+改名（现状语义）
                save_bot_credentials(row[0], {"app_id": app_id, "app_secret": app_secret})
                conn.execute(
                    "UPDATE im_bot_config SET name=%s, enabled=true, updated_at=now() WHERE id=%s",
                    (app_name, row[0]))
                conn.commit()
                _audit(owner_user_id, "im_rescan", row[0], app_id)
        done_payload = {"status": "done", "app_id": app_id, "owned": True}
        if bind_code:
            done_payload["bind_code"] = bind_code   # 前端显示码（ticket 归属已绑定,他人轮询 404）
        _set_session(session_id, done_payload, expire=900, owner_user_id=owner_user_id)   # 与码 TTL 900 对齐
        logger.info(f"feishu register done: app_id={app_id}")
    except Exception as e:
        _set_session(session_id, {"status": "error", "error": str(e), "code": "ONBOARDING_FAILED"},
                     owner_user_id=owner_user_id)
        if on_error:
            try: on_error(str(e))
            except Exception: pass
        logger.error(f"feishu register 失败: {e}")
    finally:
        _watchdog.cancel()
        release_onboarding_slot()   # 批12A：全局帽归还（线程包装器侧 acquire）


def _audit(owner_user_id, action: str, bot_id, app_id: str) -> None:
    """task 侧审计（批11D B-P2-5④）。复用 data_platform.audit_log 正源——
    盲审 P1-2：原 INSERT 列名 username 不存在（表为 actor/target），静默全灭。"""
    try:
        from src.data_platform.audit import audit_log
        who = f"task(owner={owner_user_id})" if owner_user_id else "task(admin)"
        audit_log(who, action, target=str(bot_id) if bot_id else app_id,
                  detail=f"bot={bot_id} app_id={app_id}")
    except Exception as e:
        logger.warning(f"task audit 失败(不影响流程): {e}")


def _current_username(uid: int | None) -> str | None:
    """app_preset 后缀用：owner_user_id → username（查不到回退 None→"用户"）。"""
    if uid is None:
        return None
    try:
        with get_conn() as conn:
            r = conn.execute("SELECT username FROM users WHERE id=%s", (uid,)).fetchone()
            return r[0] if r else None
    except Exception:
        return None


def sweep_stale_sessions() -> int:
    """web startup 扫（批12A #8③——A-P1-2）：非终态 session 改写 error/ONBOARDING_INTERRUPTED。

    发布重启杀 daemon 线程 → 死会话（线程里的凭证永远到不了 DB=白扫）；不扫则 #8 的
    existing_ticket 会把死人还给前端"恢复"——频控困局加重版。startup 钩子装一次。"""
    n = 0
    try:
        for key in _redis.scan_iter("feishu:session:*", count=100):
            try:
                d = json.loads(_redis.get(key) or "{}")
            except Exception:
                continue
            if not isinstance(d, dict):
                continue   # 盲审 A-P2-1：非字典载荷（null/数字）防 AttributeError 中断全扫
            if d.get("status") in ("pending", "scanning", "confirming"):
                d.update(status="error", code="ONBOARDING_INTERRUPTED",
                         error="服务重启导致会话中断，请重新发起")
                ttl = _redis.ttl(key)
                _redis.setex(key, max(ttl, 60), json.dumps(d, ensure_ascii=False))
                n += 1
    except Exception as e:
        logger.warning(f"startup 会话清扫失败(不影响启动): {e}")
    return n
