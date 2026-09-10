"""IM 通用消息处理链（批13 抽取，自 feishu_client.process_message_async 平台无关化）。

职责：首见留痕 → 身份解析（per-bot，fail-closed）→ LLM 网关工具 loop → 回复。
平台差异全部经参数注入：
- reply: Callable[[str], bool]  文本回复（截断规则由各平台闭包自理——B-P2-2/A-P2-6）
- confirm_card: Callable[[tool, args], None] | None  操作确认卡片；None=降级文本拒答
  （读类执行完再拒答操作类，与飞书"发卡后 return"行为对齐——A-P2-2）
- chat_type: 'p2p' | 'group'（runner 侧归一：飞书 p2p / 钉钉 '1' / 企微 'single'——B-P2-3）

飞书特例（bindcode 验证码绑定）留在飞书薄壳 feishu_client.process_message_async，
不进通用层（盲审 A-P2-3：平台特例不渗入通用签名）。
"""
from __future__ import annotations
import logging

logger = logging.getLogger("im_bot.handlers")

# 批13 文案师：用户面平台名（"feishu" 裸串不该见人）
_PROVIDER_NAME = {"feishu": "飞书", "wecom": "企业微信", "dingtalk": "钉钉"}


def _get_max_tool_turns() -> int:
    """从 system_config 表读取 LLM 最大工具调用轮次，默认 5。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='llm_max_tool_turns'")
            row = cur.fetchone()
            if row:
                return int(row[0])
    except Exception:
        pass
    return 5


def _first_seen_note(bot_id: int, im_user_id: str) -> None:
    """首见留痕（user_id NULL 行）——不授权，仅供 owner 个人中心"待绑定"列表一键绑定。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            if not conn.execute("SELECT 1 FROM im_bot_users WHERE bot_id=%s AND im_user_id=%s",
                                (bot_id, im_user_id)).fetchone():
                conn.execute("INSERT INTO im_bot_users (bot_id, im_user_id, role) VALUES (%s,%s,'viewer')",
                             (bot_id, im_user_id))
                conn.commit()
                logger.info("首见留痕 bot=%s im_user=%s…（待绑定，不授权）", bot_id, im_user_id[:10])
    except Exception as e:
        logger.warning("首见留痕失败(不影响拒答): %s", e)


def execute_read_tool(name: str, args: dict) -> str:
    """执行读类工具（直接查询，无副作用）。操作类走 execute_confirmed_tool（用户确认后）。
    （批13 自 feishu_client 随迁——内容平台无关，留原地会让通用层反向依赖平台实现，A-P2-4）"""
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


def handle_incoming(provider: str, bot_id: int, im_user_id: str, text: str,
                    reply, chat_type: str, *, confirm_card=None) -> None:
    """平台无关消息处理（在调用方线程里跑——runners 各自起线程）。

    chat_type（'p2p'|'group' 归一）：预留参数——飞书 bindcode 分支用 p2p 判据（在飞书壳内，
    到这里的 chat_type 暂不参与分支；群聊场景化文案/档位控制启用时接线——盲审 A-P2 标注）。"""
    if bot_id:
        _first_seen_note(bot_id, im_user_id)
    from src.im_bot.users import resolve_im_identity
    identity = resolve_im_identity(im_user_id, bot_id)
    if not identity:
        # 文案分流：自有 bot 指引个人中心绑定；平台级 bot 指引找管理员（A-P0-1 修语义随迁）
        # 批13 文案师重写：读者视角+禁内部术语+动作具体（原"未绑定平台账号/IM 标识"是系统视角）
        _own = False
        if bot_id:
            try:
                from src.data_platform.db import get_conn
                with get_conn() as conn:
                    _own = bool(conn.execute(
                        "SELECT 1 FROM im_bot_config WHERE id=%s AND owner_user_id IS NOT NULL",
                        (bot_id,)).fetchone())
            except Exception:
                pass
        _pname = _PROVIDER_NAME.get(provider, provider)
        _guide = (f"打开网页端「个人中心 → IM 通道」，在这个通道的「待绑定」里点「绑定」，绑定后即可对话。" if _own
                  else f"请联系管理员，把上面这串标识绑定到你的账号。")
        reply(f"这个机器人还不知道你是谁，暂时无法对话。\n你的{_pname}标识：{im_user_id}\n{_guide}")
        return
    role = identity["role"]
    perms = identity["perms"]

    try:
        from src.llm_gateway import gateway
        from src.llm_gateway.gateway import READ_TOOLS, OPERATIONAL_TOOLS
        operational_names = {t.name for t in OPERATIONAL_TOOLS}
        messages = [{"role": "user", "content": text}]
        resp = None
        max_turns = _get_max_tool_turns()
        for _ in range(max_turns):
            # caller 参数化（批13 A-P2-1/B-P2-1 双判，arch-19 §4）：审计/观测记真实平台
            resp = gateway.chat(messages, role=role, tools=READ_TOOLS, caller=provider, perms=perms)
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
                    if confirm_card is not None:
                        confirm_card(tc["name"], tc.get("arguments", {}))
                    has_operational = True
                else:
                    result = execute_read_tool(tc["name"], tc.get("arguments", {}))
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            if has_operational:
                if confirm_card is None:
                    # 降级（钉钉/企微 MVP，方案 §2.3）：读类已执行完，操作类文本拒答（与飞书"发卡 return"对齐）
                    reply("聊天里目前只能查询，不能执行操作。停止策略、熔断这类操作，请到网页端完成。")
                return  # 操作类等用户确认（或已降级拒答），不继续 loop
        if resp and resp.content:
            reply(resp.content)
        else:
            reply("刚才没有生成回答，请把消息重新发一遍")
    except Exception as e:
        logger.error("%s 消息处理失败: %s", provider, e)
        reply("处理出了点问题，这条没有成功。请稍后重发一遍；若一直失败，请联系管理员")   # 文案师：异常原文只进日志不进用户面
