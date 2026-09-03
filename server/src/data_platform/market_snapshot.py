"""非池标的实时行情（三档第三档项 13，U-2 修正）。

U-2 裁定核心：非池实时价不走 Tushare pro.daily（盘中空是硬伤）。
实施选型（2026-08-20 实测）：原蓝图 akshare 东财快照被反爬升级 RST
（stock_zh_a_spot_em 无 UA 裸调断连；push2 clist 单页钳 100 全市场需 59 页；
stock/get 对非浏览器 TLS 一律断）→ 改腾讯 qt.gtimg.cn 单股按需：
1 次请求自带五档+涨跌停价，多年公开接口无反爬，比全市场快照更贴三档"打开才拉"心智。

层位（arch-17 §2 裁定）：数据平台层，消费方不寄生 web_api。
失败降级返回 None（详情页 quote 降级链 hub tick→腾讯→null）。
"""
from __future__ import annotations
import json
import logging
import os

logger = logging.getLogger("data_platform.market_snapshot")

QUOTE_KEY_PREFIX = "quote:tencent:"
QUOTE_TTL = 60           # 单股轻量，60s 新鲜度（池内标的走 hub tick 秒级）

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _r():
    """模块级单例（O 审 M7：防每请求新建连接池）。"""
    global _R
    import redis
    if _R is None:
        _R = redis.Redis.from_url(
            os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True, socket_timeout=5)
    return _R


_R = None


def _tencent_sym(ts_code: str) -> str:
    """ts_code → 腾讯符号（600000.SH→sh600000）。"""
    code, _, ex = ts_code.partition(".")
    return f"{ex.lower()}{code}"


def _f(parts: list, i: int, cast=float):
    try:
        v = parts[i]
        return cast(v) if v not in ("", "-") else None
    except (ValueError, IndexError):
        return None


def get_quote(ts_code: str, force: bool = False) -> dict | None:
    """单标的实时行情（价/涨跌幅/五档/涨跌停/换手/市值），Valkey 60s TTL。

    返回 None=源不可达或代码无效（降级语义，调用方走下一级）。
    O 审修正：volume/amount 归一到股/元（腾讯原始 p[6]=手、p[37]=万元——与 hub XTP
    口径一致，防降级链切换时展示翻 100 倍）；失败负缓存 "null" 30s（M8：防腾讯
    不可达期间每请求同步等 5s timeout）。
    """
    r = _r()
    key = QUOTE_KEY_PREFIX + ts_code
    if not force:
        try:
            cached = r.get(key)
            if cached:
                return None if cached == "null" else json.loads(cached)
        except Exception as e:
            logger.warning("行情缓存读失败（直拉）: %s", e)
    try:
        import requests
        resp = requests.get(f"https://qt.gtimg.cn/q={_tencent_sym(ts_code)}",
                            headers=_HEADERS, timeout=5)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")
    except Exception as e:
        logger.warning("腾讯行情拉取失败 %s（降级 None）: %s", ts_code, e)
        try:
            r.set(key, "null", ex=30)
        except Exception:
            pass
        return None
    # v_sh600000="1~浦发银行~600000~..." ~ 分隔
    try:
        body = text.split('"')[1]
    except IndexError:
        return None
    p = body.split("~")
    if len(p) < 49:
        return None
    quote = {
        "ts": f"{p[30][0:4]}-{p[30][4:6]}-{p[30][6:8]}T{p[30][8:10]}:{p[30][10:12]}:{p[30][12:14]}+08:00",
        "name": p[1], "code": p[2],
        "last": _f(p, 3), "pre_close": _f(p, 4), "open": _f(p, 5),
        "volume": _f(p, 6, lambda v: float(v) * 100),   # 手 → 股
        "amount": _f(p, 37, lambda v: float(v) * 10000),  # 万元 → 元
        "high": _f(p, 33), "low": _f(p, 34),
        "chg": _f(p, 31), "pct_chg": _f(p, 32),
        "upper_limit": _f(p, 47), "lower_limit": _f(p, 48),
        "turnover_rate": _f(p, 38), "pe": _f(p, 39),
        "float_mv": _f(p, 44), "total_mv": _f(p, 45),      # 亿元
        "bid": [_f(p, i) for i in range(9, 19, 2)],
        "bid_v": [_f(p, i) for i in range(10, 20, 2)],
        "ask": [_f(p, i) for i in range(19, 29, 2)],
        "ask_v": [_f(p, i) for i in range(20, 30, 2)],
        "source": "tencent",
    }
    if quote["last"] is None:
        try:
            r.set(key, "null", ex=30)   # 无效代码同样负缓存（补盲审 B1）
        except Exception:
            pass
        return None
    try:
        r.set(key, json.dumps(quote, ensure_ascii=False), ex=QUOTE_TTL)
    except Exception as e:
        logger.warning("行情缓存写失败（不阻断）: %s", e)
    return quote
