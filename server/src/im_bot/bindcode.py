"""IM 绑定验证码（批11E v2——方案双盲审 A/B 修订全吸收）。

单点模块：键模板/codegen/redis 工厂/match_consume 全在这（B-P1-3：禁两处漂移）。
- 键 `im:bindcode:{bot_id}` 值=码本身（A/B 同判 P0：原稿存 owner=匹配器无物可比），TTL 900s
- 成功即消费：GETDEL 原子（A-P0-1——并发双发送者先到独得）；错 5 次 DEL（A-P1-3 防在线枚举）
- owner 一律 DB 现读（A-P1-1：bot 转归属后旧键不绑错人）
- 码禁入 logger/audit（A-P1-2）
"""
from __future__ import annotations

import logging
import re
import secrets
import unicodedata

logger = logging.getLogger("im_bot.bindcode")

_TTL_S = 900
_MAX_FAILS = 5
_MENTION_RE = re.compile(r"@_user_\d+\s?")
_redis = None


def _client():
    """与 tasks.py 同源构造（VALKEY_URL 同缺省）——单点防 db 号漂移（B-P1-3）。"""
    global _redis
    import os
    import redis as _redis_mod
    if _redis is None:
        _redis = _redis_mod.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4"),
            decode_responses=True)
    return _redis


def _key(bot_id: int) -> str:
    return f"im:bindcode:{bot_id}"


def gen_code() -> str:
    """6 位数字（secrets——A-P2 防可预测）。"""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def issue(bot_id: int, code: str) -> None:
    """向导 done 发码（键值=码,TTL 900）。调用方负责 session 载荷同码双写。"""
    _client().setex(_key(bot_id), _TTL_S, code)


def revoke(bot_id: int) -> None:
    """手动绑定成功/其他失效路径同 DEL（A-P0-1 附带）。"""
    try:
        _client().delete(_key(bot_id))
    except Exception:
        pass


def _normalize(text: str) -> str:
    """B-P1-2：NFKC 全角→半角 + 剥 @_user_N mention 前缀 + 去全部空白。"""
    s = unicodedata.normalize("NFKC", text or "")
    s = _MENTION_RE.sub("", s)
    return re.sub(r"\s+", "", s)


def match_consume(bot_id: int | None, text: str, chat_is_p2p: bool) -> bool:
    """拒答分支入口：p2p 且码匹配 → GETDEL 原子消费。返回是否命中（命中后调用方执行绑定）。

    - fid=None（webhook 路径）不触发（B-P2 防呆）
    - 码不匹配：错次 INCR，≥_MAX_FAILS DEL 键（A-P1-3）
    - Redis 异常 fail-closed 返回 False（不阻断原拒答流）
    """
    if bot_id is None or not chat_is_p2p:
        return False
    try:
        r = _client()
        code = r.get(_key(bot_id))
        if not code:
            return False
        if _normalize(text) != code:
            fails = r.incr(f"{_key(bot_id)}:fails")
            r.expire(f"{_key(bot_id)}:fails", _TTL_S)
            if int(fails) >= _MAX_FAILS:
                r.delete(_key(bot_id), f"{_key(bot_id)}:fails")
                logger.info("bindcode 错次达上限,码作废 bot=%s", bot_id)   # 不记码
            return False
        got = r.getdel(_key(bot_id))            # GETDEL：先到者独得（A-P0-1）
        try:
            r.delete(f"{_key(bot_id)}:fails")    # best-effort（A-P2-3：清理失败不吞命中结果）
        except Exception:
            pass
        return got == code
    except Exception as e:
        logger.warning("bindcode 匹配失败 fail-closed bot=%s: %s", bot_id, e)
        return False
