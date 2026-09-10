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

    def send_card(self, receive_id: str, card: dict, receive_id_type: str = "open_id"):
        """发送交互卡片（操作确认）。"""
        token = self._get_token()
        if not token:
            return
        try:
            httpx.post(
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


def check_user(open_id: str) -> str | None:
    """检查飞书用户是否授权，返回角色或 None。

    批 1 语义：先查 im_bot_users（全部 feishu bot 行的授权并集——webhook 暂无 per-bot
    路由，批 2 的 URL bid 才精确 per-bot），查无回落 env 层，再无=未授权（fail-closed）。
    """
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT u.role FROM im_bot_users u JOIN im_bot_config b ON b.id=u.bot_id "
                "WHERE u.im_user_id=%s AND b.provider='feishu' AND b.enabled "
                "ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'trader' THEN 1 "
                "WHEN 'analyst' THEN 2 ELSE 3 END LIMIT 1", (open_id,))
            row = cur.fetchone()
            if row:
                return row[0]
    except Exception as e:
        logger.warning("im_bot_users 查询失败（回落 env 授权层）: %s", e)
    if not FEISHU_USERS:
        load_feishu_users()
    return FEISHU_USERS.get(open_id)


# ——— 签名校验（批 1：主源 im_bot_config，env 兜底——arch-19 v2 §5 过渡双轨）———

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

def build_confirm_card(tool_name: str, args: dict, reason: str = "") -> dict:
    """构建操作确认卡片。按钮 value 携带 ts：确认时校验 60s 时效（SD2，F-33 防重放）。"""
    import time as _t
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"⚠️ 操作确认: {tool_name}"},
            "template": "red",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
             "content": f"**操作**: {tool_name}\n**参数**: {json.dumps(args, ensure_ascii=False)}\n**原因**: {reason or 'LLM 触发'}"}},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 确认执行"},
                 "type": "primary", "value": {"action": "confirm", "tool": tool_name, "args": args, "ts": int(_t.time())}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 取消"},
                 "type": "danger", "value": {"action": "cancel", "tool": tool_name, "ts": int(_t.time())}},
            ]},
        ],
    }


def card_action_fresh(value: dict, max_age_s: int = 60) -> bool:
    """SD2（F-33）：卡片按钮时效校验。无 ts 的旧卡片一律视为过期（部署前发出的卡片不可重放）。"""
    ts = value.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    import time as _t
    return (_t.time() - ts) <= max_age_s


def process_message_async(open_id: str, text: str, receive_id_type: str = "open_id", receive_id: str = None, fid: int = None, chat_type: str = ""):
    """飞书消息处理薄壳（批13）：bindcode 特例在壳内，通用链走 handlers.handle_incoming。

    webhook(ws_client/router) 双路径签名零改动。"""
    if receive_id is None: receive_id = open_id
    print(f"=== process_message_async: fid={fid} open_id={open_id} receive_id={receive_id} type={receive_id_type}", flush=True)
    client = get_feishu_client(fid)   # 批 2:per-bot 单例(修多 bot 回复走错凭证隐患)
    from src.im_bot.users import resolve_im_identity
    identity = resolve_im_identity(open_id, fid)   # per-bot 收口（批13 P0）
    if not identity and fid and chat_type == "p2p":
        # 批11E：验证码自动绑定（飞书特例，留壳内——盲审 A-P2-3 平台特例不进通用层）
        # p2p 判据=chat_type（代码盲审 P0-3：p2p 也有 chat_id,receive_id_type 判不出）
        # 码错不绑走原拒答；消费后整段兜底（盲审 P2-2：失败不静默烧码,明确引导手动绑定）
        try:
            from src.im_bot.bindcode import match_consume
            if match_consume(fid, text, True):
                from src.data_platform.db import get_conn as _gc
                with _gc() as conn:
                    _owner = conn.execute(
                        "SELECT owner_user_id FROM im_bot_config WHERE id=%s", (fid,)).fetchone()[0]
                from src.im_bot.users import bind_owner
                from src.data_platform.db import get_conn as _gc
                with _gc() as conn:
                    # 已绑拦截（防二次抢绑——码 GETDEL 单次已串行化并发,此处为防御纵深）
                    _bound = conn.execute(
                        "SELECT 1 FROM im_bot_users WHERE bot_id=%s AND user_id IS NOT NULL LIMIT 1",
                        (fid,)).fetchone()
                if _owner is not None and not _bound:
                    # bind_owner 原语=ON CONFLICT DO UPDATE：首见留痕 NULL 行就地转绑定行（盲审 P0-2：
                    # 内联裸 INSERT 撞 (bot_id,im_user_id) 唯一约束必崩）——pending 计数随之归零
                    bind_owner(fid, open_id, _owner)
                    if resolve_im_identity(open_id, fid):
                        from src.data_platform.audit import audit_log
                        audit_log(f"im(bot#{fid})", "owner_im_bind_autocode",
                                  target=str(_owner), detail=f"open_id={open_id[:8]}…")   # 不含码
                        client.send_text(receive_id,
                                         "✅ 绑定成功！现在发消息就以你的账号权限执行——试试「查一下风控状态」",
                                         receive_id_type)
                        return
        except Exception as e:
            logger.warning("验证码自动绑定异常（码可能已消费,请走手动绑定兜底）: %s", e)
            # 盲审 A-P2：异常若发生在 bind 之后，重查成功即已绑定——原样 fall-through 会把
            # 验证码原文喂进 LLM 对话（原实现回落引导文案）；改明确提示并 return
            if resolve_im_identity(open_id, fid):
                client.send_text(receive_id, "✅ 绑定成功（过程中略有延迟，不影响结果）。现在可以直接发消息了",
                                 receive_id_type)
                return

    from src.im_bot.handlers import handle_incoming
    handle_incoming(
        "feishu", fid, open_id, text,
        reply=lambda t: client.send_text(receive_id, t[:4000], receive_id_type),   # 截断留飞书侧（B-P2-2）
        chat_type="p2p" if chat_type == "p2p" else "group",
        confirm_card=lambda tool, args: client.send_card(
            receive_id, build_confirm_card(tool, args), receive_id_type),
    )


def execute_confirmed_tool(open_id: str, tool_name: str, args: str):
    """用户点击确认后执行操作类工具（P3-11 含 60s 超时检查）。

    P0-2 顺带修（审计 B5）：args 原样拼 systemd 单元名永远畸形——json 解析取 id。
    """
    import time
    import json as _json
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
            RiskControl.get().emergency_halt(f"飞书:{open_id}")
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
        audit_log(f"feishu:{open_id}", tool_name, detail=json.dumps(args))
    except Exception as e:
        client.send_text(open_id, f"❌ 执行失败: {e}")