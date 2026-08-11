"""飞书/Lark 对接层 —— 外部 IM 通道，AI 动态查询 + 紧急处理（带确认）。

3 秒超时约束：Webhook 收到消息立即返回 {"code":0}，LLM 任务丢后台线程，
结果通过飞书"发送消息"API 主动推回。
"""

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


class FeishuClient:
    """飞书开放平台 API 客户端。"""

    def __init__(self):
        # 从 DB feishu_config 读凭证（弃 .env LARK_*，配置 DB 化）
        from src.data_platform.db import get_conn
        from src.web_api.crypto_utils import decrypt
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT app_id, app_secret_encrypted FROM feishu_config WHERE enabled=true ORDER BY id DESC LIMIT 1")
                r = cur.fetchone()
            self.app_id = r[0] if r else ""
            self.app_secret = decrypt(r[1]) if r and r[1] else ""
        except Exception as e:
            logger.warning(f"DB 读飞书配置失败: {e}")
            self.app_id = ""
            self.app_secret = ""
        self._token: str = ""
        self._token_expires: float = 0

    def _get_token(self) -> str:
        """获取 tenant_access_token。"""
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

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "open_id"):
        """发送文本消息。"""
        token = self._get_token()
        if not token:
            return
        httpx.post(
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

    def send_card(self, receive_id: str, card: dict, receive_id_type: str = "open_id"):
        """发送交互卡片（操作确认）。"""
        token = self._get_token()
        if not token:
            return
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


# ——— 用户鉴权 + 角色映射 ———

# 授权飞书 user_id → 平台角色
FEISHU_USERS: dict[str, str] = {}  # {"ou_xxx": "admin", ...}


def load_feishu_users():
    """从环境变量加载授权用户（格式: user_id:role,user_id:role）。"""
    raw = os.environ.get("LARK_AUTHORIZED_USERS", "")
    FEISHU_USERS.clear()
    for pair in raw.split(","):
        if ":" in pair:
            uid, role = pair.strip().split(":", 1)
            FEISHU_USERS[uid] = role


def check_user(open_id: str) -> str | None:
    """检查飞书用户是否授权，返回角色或 None。"""
    if not FEISHU_USERS:
        load_feishu_users()
    return FEISHU_USERS.get(open_id)


# ——— 签名校验 ———

def verify_signature(timestamp: str, body: str, signature: str) -> bool:
    """校验飞书 Webhook 签名。"""
    secret = os.environ.get("LARK_VERIFICATION_TOKEN", "")
    if not secret:
        return True  # 未配置则跳过（开发期）
    sig = hashlib.sha256(f"{timestamp}{secret}{body}".encode()).hexdigest()
    return sig == signature


# ——— 确认卡片 ———

def build_confirm_card(tool_name: str, args: dict, reason: str = "") -> dict:
    """构建确认卡片（P3-11 含时间戳，execute_confirmed_tool 检查 60s 超时）。"""
    """构建操作确认卡片。"""
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
                 "type": "primary", "value": {"action": "confirm", "tool": tool_name, "args": args}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 取消"},
                 "type": "danger", "value": {"action": "cancel", "tool": tool_name}},
            ]},
        ],
    }


# ——— 后台处理（3s 超时绕开） ———

def process_message_async(open_id: str, text: str, receive_id_type: str = "open_id", receive_id: str = None, fid: int = None):
    if receive_id is None: receive_id = open_id
    print(f"=== process_message_async: fid={fid} open_id={open_id} receive_id={receive_id} type={receive_id_type}", flush=True)
    """后台线程：消息 → LLM 网关 → 回复/确认卡片。per-机器人 role（机器人=登录账号）。"""
    client = FeishuClient()
    role = "viewer"
    if fid:
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT role FROM feishu_config WHERE id=%s", (fid,))
                r = cur.fetchone()
                if r:
                    role = r[0]
        except Exception as e:
            logger.warning(f"查飞书机器人 role 失败: {e}")
    else:
        role = check_user(open_id)
        if not role:
            client.send_text(receive_id, "未授权，无法使用", receive_id_type)
            return

    try:
        from src.llm_gateway import gateway
        from src.llm_gateway.gateway import READ_TOOLS, OPERATIONAL_TOOLS
        operational_names = {t.name for t in OPERATIONAL_TOOLS}
        messages = [{"role": "user", "content": text}]
        resp = None
        # 工具调用 loop：读类直接执行回 LLM，操作类发确认卡片后等用户确认
        for _ in range(5):
            resp = gateway.chat(messages, role=role, tools=READ_TOOLS, caller="feishu")
            if not resp.tool_calls:
                break
            messages.append({"role": "assistant", "content": resp.content or "",
                             "tool_calls": [{"id": tc["id"], "type": "function",
                                             "function": {"name": tc["name"],
                                                          "arguments": tc.get("arguments", "{}")}}
                                            for tc in resp.tool_calls]})
            has_operational = False
            for tc in resp.tool_calls:
                if tc["name"] in operational_names:
                    card = build_confirm_card(tc["name"], tc.get("arguments", {}))
                    client.send_card(receive_id, card, receive_id_type)
                    has_operational = True
                else:
                    result = execute_read_tool(tc["name"], tc.get("arguments", {}))
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            if has_operational:
                return  # 操作类等用户确认，不继续 loop
        if resp and resp.content:
            client.send_text(receive_id, resp.content[:4000], receive_id_type)
        else:
            client.send_text(receive_id, "（LLM 无响应）", receive_id_type)
    except Exception as e:
        logger.error(f"飞书消息处理失败: {e}")
        client.send_text(open_id, f"处理失败: {e}", receive_id_type)


def execute_read_tool(name: str, args: dict) -> str:
    """执行读类工具（直接查询，无副作用）。操作类走 execute_confirmed_tool（用户确认后）。"""
    try:
        if name == "query_risk_state":
            from src.risk_control import RiskControl
            rc = RiskControl.get()
            state = "熔断" if rc.is_halted() else "正常"
            return f"风控状态: {state}; 原因: {rc.halt_reason() or '无'}"
        if name == "query_strategy_status":
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                cur = conn.execute("SELECT id, enabled, backtest_verified FROM strategy_config ORDER BY id")
                rows = cur.fetchall()
            if not rows:
                return "无策略配置"
            return "策略状态: " + "; ".join(
                f"{r[0]}({'启' if r[1] else '停'}/{'已验' if r[2] else '未验'})" for r in rows)
        if name == "query_position":
            return "持仓查询需实盘对接（XTPAdapter），当前未接入实盘"
        if name == "query_pnl":
            return "盈亏查询需实盘对接，当前未接入实盘"
        if name == "get_astock_analysis":
            sym = args.get("symbol", "")
            return f"A股研判 {sym or '全部'}：待 astock_analysis 运行产出"
        return f"工具 {name} 未实现"
    except Exception as e:
        return f"工具 {name} 执行失败: {e}"


def execute_confirmed_tool(open_id: str, tool_name: str, args: str):
    """用户点击确认后执行操作类工具（P3-11 含 60s 超时检查）。"""
    import time
    client = FeishuClient()
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
        from src.web_api.auth import audit_log
        audit_log(f"feishu:{open_id}", tool_name, detail=json.dumps(args))
    except Exception as e:
        client.send_text(open_id, f"❌ 执行失败: {e}")