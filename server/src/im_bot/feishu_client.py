"""飞书客户端/消息处理/签名/授权——自 feishu_bot/bot.py 下沉(双盲 B-S3 分层修正:
im_bot 为层 3 服务层,不得 import 层 4 入口 feishu_bot;feishu_bot/bot.py 变 re-export 薄壳,
旧引用(router/ws_client/tests)零改动继续工作)。"""

from __future__ import annotations
import os
import json
import time
import hashlib
import threading
import logging
from typing import Any
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("feishu_bot")
_token_lock = threading.Lock()


# ——— 后台处理（3s 超时绕开） ———
# 批13：_get_max_tool_turns/_first_seen_note/execute_read_tool 已随通用链迁 handlers.py；
# execute_read_tool 保留 re-export（旧引用兼容），其余为内部细节不保符号。
from src.im_bot.handlers import execute_read_tool  # noqa: F401  (re-export)


# 批 2(arch-19 v2):per-bot 客户端单例——修两个现状隐患:①多 bot 时 FeishuClient() 不带
# fid 回复走"最新 enabled"的凭证而非收消息的 bot;②每消息 new 实例 token 缓存形同虚设。
# 双盲 A-S1/B-G3:TTL 300s——凭证热更新(Web 改/重扫)最多 5 分钟后生效,跨进程各自过期;
# 即时生效走 stop/start 端点(systemctl 重启进程清缓存)。
_CLIENTS: dict[int | None, tuple["FeishuClient", float]] = {}
_clients_lock = threading.Lock()
_CLIENT_TTL = 300.0


def get_feishu_client(bot_id: int | None = None) -> "FeishuClient":
    """per-bot FeishuClient 单例(TTL 300s;bot_id=None=最新 enabled 行,兼容旧调用)。"""
    import time as _time
    with _clients_lock:
        now = _time.time()
        hit = _CLIENTS.get(bot_id)
        if hit and now - hit[1] < _CLIENT_TTL:
            return hit[0]
        client = FeishuClient(bot_id)
        _CLIENTS[bot_id] = (client, now)
        return client


def evict_feishu_client(bot_id: int | None) -> None:
    """凭证写路径后主动失效(同进程即时生效;跨进程靠 TTL/重启)。"""
    with _clients_lock:
        _CLIENTS.pop(bot_id, None)


class FeishuClient:
    """飞书开放平台 API 客户端(批 2:凭证读 im_bot_config 统一表)。"""

    def __init__(self, bot_id: int | None = None):
        from src.im_bot.credentials import get_bot_credentials
        from src.data_platform.db import get_conn
        creds = {}
        try:
            if bot_id is None:
                # 批11C（B-P2-6）：None 语义钉平台级（owner NULL）——webhook/聊天兜底路径不被
                # 自助 bot（id 更大）劫持回复凭证；无平台级 bot 回落最新 enabled（过渡兼容）
                with get_conn() as conn:
                    cur = conn.execute(
                        "SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled "
                        "AND owner_user_id IS NULL ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                    if not row:
                        cur = conn.execute(
                            "SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled "
                            "ORDER BY id DESC LIMIT 1")
                        row = cur.fetchone()
                bot_id = row[0] if row else None
            if bot_id is not None:
                creds = get_bot_credentials(bot_id)
        except Exception as e:
            logger.warning(f"DB 读飞书配置失败: {e}")
        self.bot_id = bot_id
        self.app_id = creds.get("app_id", "")
        self.app_secret = creds.get("app_secret", "")
        self._token: str = ""
        self._token_expires: float = 0

    def _get_token(self) -> str:
        """获取 tenant_access_token。"""
        with _token_lock:
            if self._token and time.time() < self._token_expires - 60:
                return self._token
            if not self.app_id or not self.app_secret:
                return ""
            try:
                resp = httpx.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    timeout=10,
                )
                data = resp.json()
                self._token = data.get("tenant_access_token", "")
                self._token_expires = time.time() + data.get("expire", 7200)
                return self._token
            except Exception as e:
                logger.error(f"获取 token 失败: {e}")
                return ""

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id") -> bool:
        """发送文本消息。返回真实结局（批 7 · A2-P2/B2-P1：原吞异常返 None——告警分发
        依赖 bool 回写审计列；现有调用点均忽略返回值，改 bool 零破坏）。"""
        token = self._get_token()
        if not token:
            return False
        try:
            resp = httpx.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning("飞书发送消息 HTTP %s: %s", resp.status_code, resp.text[:120])
                return False
            body = resp.json()
            if body.get("code") != 0:
                logger.warning("飞书发送消息 code=%s: %s", body.get("code"), str(body.get("msg", ""))[:120])
                return False
            return True
        except httpx.HTTPError as e:
            logger.warning("飞书发送消息失败: %s", e)
            return False

    def send_card(self, receive_id: str, card: dict, receive_id_type: str = "open_id") -> bool:
        """发送交互卡片（操作确认）。批27-4：补响应校验——2.0 卡首发被飞书拒时若零观测，
        真机验证期既无卡也无降级文本无日志（代码盲审 B-P1）——HTTP 状态/body code 落
        warning 并返回 False（对齐 send_text 先例）。"""
        token = self._get_token()
        if not token:
            return False
        try:
            resp = httpx.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card),
                },
                timeout=10,
            )
        except httpx.HTTPError as e:
            logger.warning("飞书发送消息失败: %s", e)
            return False
        if resp.status_code != 200:
            logger.warning("飞书发送卡片 HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        try:
            body = resp.json()
        except Exception:
            logger.warning("飞书发送卡片响应非 JSON: %s", resp.text[:200])
            return False
        if body.get("code") != 0:
            logger.warning("飞书发送卡片 code=%s: %s", body.get("code"), str(body.get("msg", ""))[:200])
            return False
        return True


# ——— 用户鉴权 + 角色映射 ———

# 授权飞书 user_id → 平台角色(env 兜底缓存;主真相源=im_bot_users 表)
FEISHU_USERS: dict[str, str] = {}  # {"ou_xxx": "admin", ...}


def load_feishu_users():
    """从环境变量加载授权用户（格式: user_id:role,user_id:role）——env 兜底层。

    IM 统一接入批 1（arch-19 v2）：主真相源=im_bot_users 表（per-bot），表空/查询失败
    回落此 env 层（过渡期双轨，批 2 退役 env）。
    """
    raw = os.environ.get("LARK_AUTHORIZED_USERS", "")
    new_users = {}
    for pair in raw.split(","):
        if ":" in pair:
            uid, role = pair.strip().split(":", 1)
            new_users[uid] = role
    FEISHU_USERS.clear()
    FEISHU_USERS.update(new_users)
    return FEISHU_USERS


# ——— 签名校验（批 1：主源 im_bot_config，env 兜底——arch-19 v2 §5 过渡双轨）———
# 批27-27：check_user（表查询+env 授权兜底）已退役——零业务调用，且 env 兜底与
# 五轮"身份源=绑定/owner 直通"裁定相悖（load_feishu_users/FEISHU_USERS 仍活：探针/users.py 兜底）

def _im_bot_secret(field: str, env_key: str) -> str:
    """取签名密钥:im_bot_config 任一 enabled feishu 行的 credentials.{field}（批 1 全局
    近似——单 bot 现状足够；批 2 URL bid 精确 per-bot）；无行/无字段/解密失败回落 env。"""
    try:
        import json as _json
        from src.quant_common.crypto import decrypt
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT credentials_encrypted FROM im_bot_config "
                "WHERE provider='feishu' AND enabled AND credentials_encrypted IS NOT NULL "
                "AND owner_user_id IS NULL "
                "ORDER BY id DESC LIMIT 1")   # 批11C（A-P1-1）：钉平台级 bot——自助 bot（id 更大）不劫持卡片验签/回执通道
            row = cur.fetchone()
            if row:
                creds = _json.loads(decrypt(row[0]))
                v = creds.get(field)
                if v:
                    return v
    except Exception as e:
        logger.warning("im_bot_config 密钥读取失败 field=%s（回落 env %s）: %s", field, env_key, e)
    return os.environ.get(env_key, "")


def verify_event_signature(header_ts: str, nonce: str, body: str, signature: str) -> bool:
    """校验飞书事件回调签名（P0 复审修正 2026-08-20，官方算法——SDK 源码级确认）：
    sha256(HTTP 头 X-Lark-Timestamp + X-Lark-Nonce + Encrypt Key + body)。

    批 1：密钥主源=im_bot_config.credentials.encrypt_key，env 兜底。"""
    secret = _im_bot_secret("encrypt_key", "LARK_ENCRYPT_KEY")
    if not secret:
        return True  # 未配置 Encrypt Key 则跳过（兼容纯 token 校验模式；卡片路径另有 fail-closed）
    sig = hashlib.sha256(f"{header_ts}{nonce}{secret}{body}".encode()).hexdigest()
    return sig == signature


def verify_card_signature(header_ts: str, nonce: str, body: str, signature: str) -> bool:
    """校验飞书卡片回调签名（P0 复审修正 2026-08-20，官方算法）：
    sha1(HTTP 头 X-Lark-Timestamp + X-Lark-Nonce + Verification Token + body)。

    批 1：密钥主源=im_bot_config.credentials.verification_token，env 兜底；
    两处皆空=fail-closed 拒（卡片是操作执行面，P0-2）。"""
    secret = _im_bot_secret("verification_token", "LARK_VERIFICATION_TOKEN")
    if not secret:
        return False   # 卡片是操作执行面：未配置即拒（fail-closed，P0-2）
    sig = hashlib.sha1(f"{header_ts}{nonce}{secret}{body}".encode()).hexdigest()
    return sig == signature


# ——— 确认卡片 ———

# 批27-4：ws 卡片回调通道就绪标志（ws_client 探针置 False——SDK 版本未验证时降级文本，
# 防止发出用户点了没反应的卡）。仅 ws 进程内生效；webhook 入口不在此机制内（无公网入口）。
CARD_CHANNEL_OK = True


def build_confirm_card(tool_name: str, args: dict, reason: str = "") -> dict:
    """构建操作确认卡片（批27-4 v3.1：CardKit 2.0——1.0 interactive 卡不走 card.action.trigger
    事件，2.0 是 ws 回调链的确定触发前提）。

    结构实证：openclaw sanitizeNativeFeishuCard 输出形态（生产实践）——按钮直挂
    body.elements（顶层无 action 键）；callback 行为 behaviors[0].value 携带 {action,tool,args,ts}，
    ts=建卡时刻秒（确认时 card_action_fresh 校验 60s 时效，SD2/F-33；同卡恒定=exec 去重键成分）。
    inline 发送（msg_type=interactive+content=卡 JSON）复用 send_card 零改动。"""
    import time as _t
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⚠️ 操作确认: {tool_name}"},
            "template": "red",
        },
        "body": {"elements": [
            {"tag": "markdown", "content":
             f"**操作**: {tool_name}\n**参数**: {json.dumps(args, ensure_ascii=False)}\n**原因**: {reason or 'LLM 触发'}"},
            {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 确认执行"},
             "type": "primary",
             "behaviors": [{"type": "callback",
                            "value": {"action": "confirm", "tool": tool_name, "args": args, "ts": int(_t.time())}}]},
            {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 取消"},
             "type": "danger",
             "behaviors": [{"type": "callback",
                            "value": {"action": "cancel", "tool": tool_name, "ts": int(_t.time())}}]},
        ]},
    }


def card_action_fresh(value: dict, max_age_s: int = 60) -> bool:
    """SD2（F-33）：卡片按钮时效校验。无 ts 的旧卡片一律视为过期（部署前发出的卡片不可重放）。"""
    ts = value.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    import time as _t
    return (_t.time() - ts) <= max_age_s


def process_message_async(open_id: str, text: str, receive_id_type: str = "open_id", receive_id: str = None, fid: int = None, chat_type: str = ""):
    """飞书消息处理薄壳（批13）：通用链走 handlers.handle_incoming（五轮：绑定机制取消，
    bindcode/首见留痕链退役——自有 bot 由 resolve owner 直通）。

    webhook(ws_client/router) 双路径签名零改动。"""
    if receive_id is None: receive_id = open_id
    logger.info("process_message_async: fid=%s open_id=%s receive_id=%s type=%s",
                fid, open_id, receive_id, receive_id_type)   # 批27-8：print→logger（调试遗留 === 格式清）
    client = get_feishu_client(fid)   # 批 2:per-bot 单例(修多 bot 回复走错凭证隐患)
    # 批26-4：删外层 resolve_im_identity 死赋值（结果从未使用——批13 迁移残留）；
    # 身份解析单点=handle_incoming 内部（handlers.py，钉钉/企微 runner 同构）
    from src.im_bot.handlers import handle_incoming

    def _confirm_card(tool: str, args: dict) -> None:
        # 批27-4：卡片回调通道未就绪（ws 探针失败）时降级文本——发出的卡点了没反应比不发更糟
        if not CARD_CHANNEL_OK:
            logger.warning("确认卡片通道未就绪，操作 %s 降级文本拒答（SDK 版本未验证?）", tool)
            client.send_text(receive_id,
                             "确认卡片暂时发不出来，这次操作没有执行。请到网页端完成这项操作。",
                             receive_id_type)
            return
        client.send_card(receive_id, build_confirm_card(tool, args), receive_id_type)

    handle_incoming(
        "feishu", fid, open_id, text,
        reply=lambda t: client.send_text(receive_id, t[:4000], receive_id_type),   # 截断留飞书侧（B-P2-2）
        chat_type="p2p" if chat_type == "p2p" else "group",
        confirm_card=_confirm_card,
    )


def execute_confirmed_tool(open_id: str, tool_name: str, args: str, username: str | None = None):
    """用户点击确认后执行操作类工具（P3-11 含 60s 超时检查）。

    P0-2 顺带修（审计 B5）：args 原样拼 systemd 单元名永远畸形——json 解析取 id。
    批27-13：username=身份解析结果（router 卡片闸已解析）——审计 actor 与 halt reason
    记可读用户名而非 open_id（盲审 B）；None 兜底回退 feishu:{open_id}。"""
    import time
    import json as _json
    _actor = username or f"feishu:{open_id}"
    try:  # args 可能是 {"id": N} 的 JSON 串或纯 id
        _a = _json.loads(args) if isinstance(args, str) and args.strip().startswith("{") else args
        _sid = _a.get("id", _a) if isinstance(_a, dict) else _a
        args = str(_sid)
    except Exception:
        pass
    # 批11C：卡片回执钉平台级 bot（owner NULL）——与 _im_bot_secret 同源（A-P1-1）
    from src.data_platform.db import get_conn as _gc
    with _gc() as conn:
        _pb = conn.execute("SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled "
                           "AND owner_user_id IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    client = get_feishu_client(_pb[0] if _pb else None)
    try:
        # 实际执行工具（emergency_halt / strategy_stop 等）
        if tool_name == "emergency_halt":
            from src.risk_control import RiskControl
            RiskControl.get().emergency_halt(f"飞书:{_actor}")
            client.send_text(open_id, "✅ 已执行熔断")
        elif tool_name == "risk_resume":
            from src.risk_control import RiskControl
            RiskControl.get().resume()
            client.send_text(open_id, "✅ 已恢复交易")
        elif tool_name == "strategy_stop":
            import subprocess
            try:
                subprocess.run(["systemctl", "stop", f"quant-strategy@{args}"], check=True, timeout=10)
                client.send_text(open_id, f"✅ 已停止策略 {args}")
            except Exception as e:
                client.send_text(open_id, f"⚠️ 停止失败（polkit 未配? 待办#14）: {e}")
        elif tool_name == "strategy_start":
            import subprocess
            try:
                subprocess.run(["systemctl", "start", f"quant-strategy@{args}"], check=True, timeout=10)
                client.send_text(open_id, f"✅ 已启动策略 {args}")
            except Exception as e:
                client.send_text(open_id, f"⚠️ 启动失败（polkit 未配? 待办#14）: {e}")
        else:
            client.send_text(open_id, f"⚠️ 未知操作: {tool_name}")
        # 审计
        from src.data_platform.audit import audit_log
        audit_log(_actor, tool_name, detail=json.dumps(args))   # 批27-13：可读 actor
    except Exception as e:
        client.send_text(open_id, f"❌ 执行失败: {e}")