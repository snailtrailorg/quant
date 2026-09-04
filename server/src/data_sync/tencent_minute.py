"""腾讯分钟线攒数据（分钟数据源重构 21 号 §3.2）。

腾讯 mkline 免费无积分限制，但 1min 只返回 320 根滚动窗口（≈1.33 交易日）——
漏一天即断 ~4 小时（早盘 09:30~13:42）。每天收盘后取一次攒进 bar_1min，供
回测/研判/暖机。将来 Tushare 分钟线开通后切换（minute_data_source='tushare'），
本任务停跑。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import requests

from src.data_platform import db as _pdb
from .sync_lock import SyncLock

logger = logging.getLogger("data_sync.tencent_minute")

TENCENT_MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={sym},m1,,320"
TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0"}
TENCENT_TIMEOUT = 5
_CURSOR_KEY = "ds:tencent:minute:cursor"   # 游标：上次扫到的 symbol，下次断点续（防尾部标的永久排不上）


def _data_source() -> str:
    """读 minute_data_source 开关（tencent/tushare）。缺省/异常回落 tencent。"""
    try:
        with _pdb.get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key='minute_data_source'")
            row = cur.fetchone()
            return row[0] if row else "tencent"
    except Exception:
        return "tencent"


def _minute_symbols() -> list[str]:
    """展开表攒数据标的（vt 格式）。"""
    with _pdb.get_conn() as conn:
        cur = conn.execute("SELECT symbol FROM minute_symbols ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


def _parse_tencent(ts_code: str) -> list[tuple]:
    """拉腾讯 1min K 线，返回 [(date, hhmm, open, high, low, close, vol股), ...]。

    字段顺序 [时间 yyyyMMddHHmm, open, close, high, low, volume(手), {}, 涨跌]——按位置
    解析（close 在 high/low 前，盲审 P2）；volume 手→股 ×100。
    """
    from src.data_platform.market_snapshot import _tencent_sym
    sym = _tencent_sym(ts_code)
    last_err = None
    for _ in range(2):   # 网络抖动重试 1 次（方案 §3.2，盲审 A-P2/B-P2）
        try:
            resp = requests.get(TENCENT_MKLINE_URL.format(sym=sym),
                                headers=TENCENT_HEADERS, timeout=TENCENT_TIMEOUT)
            resp.raise_for_status()
            break
        except Exception as e:
            last_err = e
    else:
        raise last_err
    data = (resp.json().get("data") or {}).get(sym) or {}
    arr = data.get("m1") or []
    out = []
    for x in arr:
        t = x[0]                     # yyyyMMddHHmm
        d, hhmm = t[:8], t[8:12]
        o, c, h, l = float(x[1]), float(x[2]), float(x[3]), float(x[4])
        v = float(x[5]) * 100        # 手 → 股
        out.append((d, hhmm, o, h, l, c, v))
    return out


def _to_rows(vt: str, bars: list[tuple]) -> list[tuple]:
    """腾讯 bar → bar_1min 11 字段元组（分钟末标注，与 bar_hub 对齐）。

    - 中间段 HHMM 直接对齐（腾讯 0931 = bar_hub 09:31 同一分钟，已 79 根 0 差异实证）
    - 收盘竞价 1500 → 写 15:01（腾讯标 1500、bar_hub 分钟末标 1501）
    - 开盘竞价 0930 → 丢弃（腾讯单独标竞价根，bar_hub 首根 0931 已含竞价）
    """
    rows = []
    for d, hhmm, o, h, l, c, v in bars:
        if hhmm == "0930":
            continue                     # 开盘竞价根丢弃
        ts_hhmm = "1501" if hhmm == "1500" else hhmm   # 收盘竞价错位
        ts = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                      int(ts_hhmm[:2]), int(ts_hhmm[2:4]))
        # amount 写 0（bar_1min.amount NOT NULL DEFAULT 0；腾讯 mkline 无成交额，
        # 对齐 Tushare to_save_rows_min 缺省 0 口径，勿写 None——盲审 B-P0 NotNullViolation）
        rows.append((vt, "1min", ts, o, h, l, c, v, 0.0, None, "tencent"))
    return rows


def _check_gap(vt: str) -> None:
    """漏取检测：bar_1min 里该标的 MAX(ts) 落后上一交易日 → 告警（320 窗口漏一天断 ~4h）。

    非交易日跳过（MAX(ts) 落后正常）；上一交易日用 trade_cal 而非日历昨天——
    周日/长假后周一不误报（盲审 A-P1/B-P2）。
    """
    from src.alert_notify.notify import safe_notify
    from src.data_platform.db import is_trading_day
    if not is_trading_day():
        return
    try:
        with _pdb.get_conn() as conn:
            cur = conn.execute("SELECT MAX(ts) FROM bar_1min WHERE symbol=%s", (vt,))
            row = cur.fetchone()
            last = row[0] if row else None
            cur = conn.execute(
                "SELECT MAX(cal_date)::date FROM trade_cal WHERE exchange='SSE' "
                "AND is_open=1 AND cal_date < CURRENT_DATE")
            prev_row = cur.fetchone()
            prev = prev_row[0] if prev_row else None
        if prev is not None and (last is None or last.date() < prev):
            safe_notify("warn", f"分钟数据漏取 {vt}",
                        f"bar_1min 最后 {last}，落后上一交易日 {prev}——腾讯 1min 滚动窗口漏一天即断档",
                        code="minute.gap")
    except Exception as e:
        logger.warning("漏取检测 %s 失败: %s", vt, e)


def _log_sync(total_saved: int, total_symbols: int, errors: list, timed_out: bool = False) -> None:
    """sync_log 可观测（对齐 pool_minute 模式，含 timeout 态）。"""
    status = "timeout" if timed_out else ("done" if not errors else "partial")
    try:
        with _pdb.get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_log (sync_id, ts, mode, start_date, end_date, rows_pulled, rows_saved, duration_ms, status, error, failed_dates) "
                "VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                ("tencent_minute", "beat", "", "", total_saved, total_saved, 0,
                 status, "", "; ".join(errors[:3])[:200]))
            conn.commit()
    except Exception as e:
        logger.error("tencent_minute sync_log 写入失败: %s", e)


def sync_tencent_minute(timebox_s: int = 280) -> dict:
    """腾讯分钟攒（每天收盘后一次；数据源开关=tencent 才跑）。

    - 扫描 minute_symbols，Valkey 游标断点续（环形轮转，防尾部标的永久排不上）
    - 逐个拉腾讯 1min → save_bars('1min', rows)（UNIQUE 幂等）
    - 漏取检测 + SyncLock 防重叠 + sync_log 可观测
    """
    if _data_source() != "tencent":
        return {"status": "disabled", "reason": "minute_data_source != tencent"}

    from src.data_platform.db import is_trading_day
    if not is_trading_day():
        return {"status": "skipped", "reason": "非交易日"}   # 盲审 A-P1：收盘 beat 周末也跑，加交易日闸门

    lock = SyncLock("tencent_minute")
    with lock:
        if not lock.acquired:
            return {"status": "skipped", "reason": "上轮仍在运行"}

        symbols = _minute_symbols()
        if not symbols:
            return {"status": "idle", "reason": "minute_symbols 为空"}

        import os as _os
        import redis as _redis
        r = _redis.Redis.from_url(
            _os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
            decode_responses=True, socket_timeout=3)

        # 游标轮转：从上次断点后开始（环形）
        cursor = r.get(_CURSOR_KEY)
        start_idx = 0
        if cursor and cursor in symbols:
            start_idx = (symbols.index(cursor) + 1) % len(symbols)
        ordered = symbols[start_idx:] + symbols[:start_idx]

        from src.data_platform.schema import vt_to_ts
        deadline = time.time() + timebox_s
        total_saved, errors, timed_out = 0, [], False
        for vt in ordered:
            if time.time() >= deadline:
                timed_out = True
                break
            try:
                bars = _parse_tencent(vt_to_ts(vt))
                rows = _to_rows(vt, bars)
                if rows:
                    # 用实际入库行数（save_bars 内 validate_bars 会剔 ohlc=0 行，盲审 A-P2/B-P2）
                    total_saved += _pdb.save_bars("1min", rows)
                r.set(_CURSOR_KEY, vt, ex=86400)
                _check_gap(vt)
            except Exception as e:
                errors.append(f"{vt}: {type(e).__name__}: {str(e)[:60]}")
                logger.warning("腾讯分钟攒 %s 失败: %s", vt, e)

        status = "timebox" if timed_out else ("done" if not errors else "partial")
        _log_sync(total_saved, len(symbols), errors, timed_out)
        return {"status": status,
                "symbols": len(symbols), "saved": total_saved, "errors": errors[:5]}
