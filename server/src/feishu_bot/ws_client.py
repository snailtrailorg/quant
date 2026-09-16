"""飞书长连接客户端（lark.ws.Client 常驻进程）。

从 DB im_bot_config 读凭证(批 2)，维持 WebSocket 长连接接收消息（不需要公网 webhook）。
systemd quant-feishu-bot@quant.service 管理（auto_reconnect=True 自动重连）。

启动：python -m src.feishu_bot.ws_client
"""
from __future__ import annotations
import json
import logging
import lark_oapi as lark
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from src.data_platform.db import get_conn
from src.feishu_bot.bot import process_message_async

logger = logging.getLogger("feishu_bot")
_FID = None  # 当前机器人 id（main 设置，on_message 用）


def load_feishu_credentials(fid=None) -> tuple[str, str]:
    """从 im_bot_config 读飞书凭证(批 2, arch-19 v2)。
    fid 指定机器人 id，None 读最新 enabled（兼容）。"""
    from src.im_bot.credentials import get_bot_credentials
    creds = get_bot_credentials(fid) if fid else {}
    if not creds:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id FROM im_bot_config WHERE provider='feishu' AND enabled "
                "ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        if row:
            creds = get_bot_credentials(row[0])
    if not creds.get("app_id") or not creds.get("app_secret"):
        raise RuntimeError("未配置飞书机器人（im_bot_config 无有效凭证），请先扫码接入或在 Web 补录")
    return creds["app_id"], creds["app_secret"]


def on_message(data) -> None:
    """处理收到的消息事件 -> process_message_async（复用 bot.py）。"""
    logger.info(f"on_message triggered: {data}")
    try:
        event = data.event
        msg = event.message
        open_id = event.sender.sender_id.open_id
        content = msg.content or "{}"
        text = json.loads(content).get("text", "")
        chat_id = getattr(msg, "chat_id", "")
        receive_id = chat_id or open_id
        receive_id_type = "chat_id" if chat_id else "open_id"
        chat_type = getattr(msg, "chat_type", "") or ""   # 批11E P0-3：p2p 也有 chat_id——私聊判别唯一真源=chat_type
        # 补审E-3：起后台线程处理（与 router/webhook 路径对齐）——原同步跑在 lark ws 的
        # asyncio 事件循环上，LLM chat 阻塞期间 ping 停发可能被服务端断连
        import threading
        threading.Thread(target=process_message_async, daemon=True,
                         args=(open_id, text, receive_id_type, receive_id, _FID, chat_type)).start()
    except Exception as e:
        import traceback
        logger.error(f"处理飞书消息失败: {e}\n{traceback.format_exc()}")   # 批27-8：print/traceback.print_exc→logger（子进程 stdout 不可观测）


# ——— 批27-4：ws 卡片回调（card.action.trigger）通道 ———
# 背景：lark SDK ws client 对非 EVENT 帧静默丢弃（_handle_data_frame `elif CARD: return`，
# 1.7.1~1.7.3 源码一致）。证据链（v3.1，双盲审全吸收）：node SDK 1.74 同样只处理 EVENT 帧 +
# openclaw 2026.9.4 生产无 monkey-patch → card.action.trigger 主流路径=EVENT 帧原生分发
# （dispatcher 公开 API register_p2_card_action_trigger 即可收到）；CARD 帧为历史路径，
# 下方 patch 条件改写兼容——双覆盖不赌飞书走哪条。真机验证=部署后专项。

_VERIFIED_SDK = {"1.7.1", "1.7.3"}   # ws client 行为已源码核过的版本（requirements >=1.3 不钉，探针防升级漂移）


def _sdk_version() -> str:
    from importlib.metadata import version
    try:
        return version("lark-oapi")
    except Exception:
        return ""


def patch_ws_card_frames(client) -> bool:
    """CARD 帧→EVENT 条件改写 monkey-patch（批27-4 v3.1，学 openclaw 历史实践）。

    仅当帧 type=card 且 payload 事件信封的 header.event_type 命中 dispatcher 注册表
    （键带 p2. 前缀——裸键恒不命中，双盲审 P0 共识）时，帧头 type 原地改 event 交原方法
    （_get_by_key 取首个——只能遍历改已有头 value，append 追加=静默死代码）。分包（sum>1）
    先 _combine 合成再 peek（末帧重入幂等，缓存 5s，单测钉住防 SDK 漂移）；peek 全包容错，
    失败/未命中=不改写维持 SDK 原行为。返回 False=SDK 版本未验证（调用方降级文本）。"""
    ver = _sdk_version()
    if ver not in _VERIFIED_SDK or not hasattr(client, "_handle_data_frame"):
        logger.warning("ws 卡片帧 patch 未装配：lark-oapi=%s（已验证集 %s）——CARD 帧历史路径不受保护",
                       ver or "未知", sorted(_VERIFIED_SDK))
        return False
    orig = client._handle_data_frame
    handler = client._event_handler
    registry = set(getattr(handler, "_callback_processor_map", None) or {}) | \
        set(getattr(handler, "_processorMap", None) or {})

    async def wrapper(frame):
        try:
            from lark_oapi.ws.enum import MessageType
            headers = {h.key: h.value for h in frame.headers}
            if headers.get("type") != MessageType.CARD.value:
                return await orig(frame)
            pl = frame.payload
            if int(headers.get("sum", "1")) > 1:
                pl = client._combine(headers.get("message_id", ""), int(headers["sum"]),
                                     int(headers.get("seq", "0")), pl)
                if pl is None:
                    return await orig(frame)   # 未集齐，交原方法走缓存合包
            et = json.loads(pl.decode("utf-8", "replace")).get("header", {}).get("event_type", "")
            if f"p2.{et}" not in registry:
                return await orig(frame)
            for h in frame.headers:
                if h.key == "type":
                    h.value = MessageType.EVENT.value
            logger.info("ws CARD 帧改写为 event 分发: event_type=%s", et)
        except Exception:   # peek 容错：任何失败=不改写不冒泡（wrap 抛异常会被 _handle_message 吞掉整帧）
            pass
        return await orig(frame)

    client._handle_data_frame = wrapper   # 实例属性遮蔽（先取 orig 再赋值防递归；跨重连存活）
    logger.info("ws 卡片帧 patch 已装配（lark-oapi=%s）", ver)
    return True


def _card_gates(event_id: str, value: dict, open_id: str, fid: int,
                mid: str = "") -> None:
    """卡片确认闸门链（批27-4 v3.1 ⑤，工作线程内跑——闸门含 redis/PG 同步 IO，上 asyncio loop
    会冻结连接断连，对齐补审E-3 同根因）。顺序钉死：解析→时效→身份→权限→dedup→执行
    （批29-3：平台级闸退役——六轮裁定卡片面归用户 bot，身份/权限闸即安全边界）。
    （dedup 放最后=handler 异常 ACK 500 重推可重走全幂等闸门）。签名闸不适用 ws——鉴权=连接级
    app_secret 握手（帧只来自已鉴权连接；卡片 value 由我方建卡写入飞书原样回传，不可注入）。
    mid=卡片消息 id（event.context.open_message_id，每卡唯一）——exec 去重键主成分（代码盲审
    A/B 共识：{ts}:{tool} 键同秒同工具的两张卡互斥，"停掉 s1 和 s2"第二张被静默吞）。"""
    from src.im_bot.feishu_client import (build_terminal_card, card_action_fresh,
                                          execute_confirmed_tool, get_feishu_client)

    def _terminal(status: str, tool_name: str = "") -> None:
        """批29b：卡片终态化（PATCH 原地更新摘按钮+状态文案）。mid 缺失跳过；fail-soft
        双保险（update_card 内部已全吞 Exception，此处再兜防 build/get 环节）。"""
        if not mid:
            return
        try:
            get_feishu_client(fid).update_card(mid, build_terminal_card(tool_name, status))
        except Exception as e:
            logger.warning("卡片终态化失败（不影响闸门行为）: %s", e)

    if value.get("action") == "cancel":
        _terminal("cancelled")   # cancel 分支在 tool 提取前——文案不带 tool，空串
        return
    if value.get("action") != "confirm":
        return   # 未知 action 维持静默（A-P2-2：终态化仅精确命中 cancel，不替脏数据圆谎）
    tool = value.get("tool", "")
    args = value.get("args", {})
    if not card_action_fresh(value):
        logger.warning("卡片确认超时/无时间戳拒绝执行: tool=%s", tool)
        _terminal("expired", tool)
        return
    from src.im_bot.users import resolve_im_identity
    identity = resolve_im_identity(open_id, fid)
    if not identity:
        logger.warning("卡片确认未绑定拒绝: open_id=%s tool=%s", open_id, tool)
        _terminal("unavailable", tool)
        return
    _need = "resume" if tool == "risk_resume" else ("halt" if tool == "emergency_halt" else "trade")
    if _need not in identity["perms"]:
        logger.warning("卡片确认权限不足拒绝执行: user=%s tool=%s need=%s",
                       identity["username"], tool, _need)
        _terminal("denied", tool)
        return
    import os
    import redis as _redis
    try:
        r = _redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
                                  decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
        if event_id and not r.set(f"feishu:card:{event_id}", "1", nx=True, ex=300):
            logger.warning("重复卡片回调丢弃: event_id=%s", event_id)
            return
        # 卡级 exec 去重（批27-4 新增，代码盲审修订）：同卡 60s 窗内连点每次生成新 event_id
        # （⑤ 挡不住）→ 一张卡只执行一次；键主成分=mid（每卡唯一），缺失回退 ts:open_id
        # （同秒不同人仍可分）——纯 {ts}:{tool} 会把"停掉 s1 和 s2"的第二张卡静默吞掉。
        # HTTP 面同病 router.py 同点同修
        _exec_key = mid or f"{value.get('ts')}:{open_id}"
        if not r.set(f"feishu:card:exec:{_exec_key}:{tool}", "1", nx=True, ex=300):
            logger.warning("同卡重复执行拦截: tool=%s mid=%s", tool, mid or "(回退 ts:open_id)")
            get_feishu_client(fid).send_text(
                open_id, "这项操作刚才已经执行过了，再点也不会执行第二次——不用再点。")   # 文案师 A 候选
            # 批29b-B1（代码盲审 B-P1-1）：据伴生结果键重刷——防首点失败的卡被重点刷绿
            # "已执行"（风险操作面说谎）；结果键缺失/redis 异常 → 不重刷（首点已终态化，
            # PATCH 失败态由过期闸兜底），保守缺省不赌方向。
            try:
                _res = r.get(f"feishu:card:execres:{_exec_key}:{tool}")
            except Exception:
                _res = None
            if _res == "ok":
                _terminal("executed", tool)
            elif _res == "fail":
                _terminal("failed", tool)
            return
    except Exception as e:
        logger.warning("卡片去重检查失败（放行，风险自负）: %s", e)   # fail-open 对齐 HTTP 面
        r = None   # 批29b-B1：redis 不可用时伴生键同样不可写——下方 execres 落键各自兜
    _ok = execute_confirmed_tool(open_id, tool, json.dumps(args) if isinstance(args, dict) else args,
                                 identity["username"], fid=fid)   # 批29-2b：回执走本 bot 凭证
    if r is not None:
        try:   # 伴生结果键（批29b-B1）：去重点据此重刷 executed/failed
            r.set(f"feishu:card:execres:{_exec_key}:{tool}", "ok" if _ok else "fail", ex=300)
        except Exception as e:
            logger.warning("卡片执行结果键写入失败（去重点将不重刷）: %s", e)
    _terminal("executed" if _ok else "failed", tool)   # 批29b：成败终态（A-P1-1——失败不终态=去重键锁死假状态）


def make_card_handler(fid: int):
    """构建 card.action.trigger 回调（闭包捕获局部 fid——不读模块级 _FID 与赋值时序解耦）。

    handler 内仅做字段提取（跑在 asyncio loop 上），闸门+执行全移工作线程。返回 None=
    ACK 200 空体；执行结果由 execute_confirmed_tool 内 send_text 回复（与 HTTP 面一致，
    不做 toast）。"""
    import threading

    def on_card(data) -> None:
        try:
            header = getattr(data, "header", None)
            event_id = getattr(header, "event_id", "") or ""
            ev = getattr(data, "event", None)
            value = dict(getattr(getattr(ev, "action", None), "value", None) or {})
            open_id = getattr(getattr(ev, "operator", None), "open_id", "") or ""
            mid = getattr(getattr(ev, "context", None), "open_message_id", "") or ""   # 卡唯一键成分
            threading.Thread(target=_card_gates, daemon=True,
                             args=(event_id, value, open_id, fid, mid)).start()
        except Exception as e:
            logger.error("卡片回调字段提取失败: %s", e)

    return on_card


def main() -> None:
    import sys   # 顶部导入（函数后段残留旧 import sys 会把 sys 变局部——12:03 prod feishu 波崩溃根因）
    # 补审E-8：单元实例名须为数字 bot id（quant-feishu-bot@{bid}）；非数字 fail-fast——
    # 原静默降级会让 _FID 污染流入 SQL DataError→首见整段死火回到零留痕盲区
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        raise SystemExit("用法: python -m src.feishu_bot.ws_client <bot_id>（数字——systemd 实例名）")
    # 2026-09-02：启动即回填（arch-19 双轨收尾——env 授权用户入表，告警 dispatch 同源可用）
    from src.im_bot.users import backfill_from_env
    backfill_from_env(int(sys.argv[1]))
    import sys, logging
    global _FID
    _FID = sys.argv[1] if len(sys.argv) > 1 else None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    """启动长连接客户端（阻塞）。"""
    app_id, app_secret = load_feishu_credentials(_FID)
    _fid = int(_FID)
    from src.im_bot import feishu_client as _fc   # 须先于后段 _fc 引用（探针赋值）——函数内后段 import 会把名字局部化（快审 P0：同款 12:03 prod 崩溃）
    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(make_card_handler(_fid))
        .build()
    )
    client = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,
        auto_reconnect=True,
    )
    # 批27-4：探针+patch（EVENT 帧路径走公开 API 与 patch 无关恒注册；False 时 ws 进程
    # confirm_card 降级文本——SDK 版本未验证期宁可不出卡不出"点了没反应"）
    _fc.CARD_CHANNEL_OK = patch_ws_card_frames(client)
    logger.info(f"飞书长连接启动: id={_FID} app_id={app_id} 卡片patch={_fc.CARD_CHANNEL_OK}")
    client.start()  # 阻塞维持连接


if __name__ == "__main__":
    main()
