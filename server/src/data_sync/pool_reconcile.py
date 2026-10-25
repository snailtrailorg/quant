"""批 137b：池族/tier1 对账（按表族判，不搞一套规则套全部）。

分族依据（用户 2026-10-22 裁定）：不同表用途不同——
  A  逐标的本业日频：每个交易日、每标的一条（cyq_chips）⇒ 逐标的判缺日
  A′ 加密日频：每天都是交易日（本模块不涉及，crypto 走 engine 的日历无关判法）
  B  全市场按日快照：交易日至少有一行（不按标的判——当天没上榜是正常）
  C  静态清单：不保证每天有新增 ⇒ 判新鲜度
  D  逐标的事件/季频：单只不会每天有 ⇒ 只判接口健康（连续 N 轮全池 0 行）

自愈边界：只有 A 族能按缺口区间补拉（_repull_symbol）；B/C/D 只落告警面。
所有判法 failure-isolated：单条查询/判定异常只记日志，绝不影响同步主流程。

**列名纪律**（批 137b 代码审 P0-1 血证）：池十表列名是 `ts_code`（0046），bar 表才是 `symbol`。
本模块不做任何列名硬编码——符号列与日期列一律从 `sync_kind_config.pk_cols` 派生，
SQL 里用的名字由 `symbol_col_of` / `date_col_of` 给出，测试断言 SQL 文本。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.data_platform.db import get_conn

logger = logging.getLogger("data_sync.pool_reconcile")

# A 族：逐标的、每个交易日应有（pool 内）
FAMILY_A: frozenset[str] = frozenset({"cyq_chips"})
# D 族：逐标的事件/季频（pool 内其余）——只判接口健康
FAMILY_D: frozenset[str] = frozenset({
    "income", "balancesheet", "cashflow", "fina_indicator",
    "top10_holders", "dividend", "pledge_stat", "share_float", "stk_holdernumber",
})
# B 族：**显式六表**（不能拿 _TIER1_BATCH 整个遍历——里面还有 cyq_perf 与 forecast：
# forecast 是业绩预告（ann_date 事件型，非披露季天天 0 行）⇒ 按日快照判必假报）。
# 批 147：三元组 (表, 日期列, lag_trade_days)——lag=1 族（margin_detail）上游 T+1 发布
# （晨档时昨日数据尚未出）⇒ 表 max 恒落后「昨日快照」假设一日，每晚误报；对账 expected
# 对 lag 族再回退一交易日对齐发布节奏（数据面零缺失，批 146 步 8 定谳）。
FAMILY_B: tuple[tuple[str, str, int], ...] = (
    ("stk_limit", "trade_date", 0),
    ("moneyflow", "trade_date", 0),
    ("margin_detail", "trade_date", 1),
    ("top_list", "trade_date", 0),
    ("block_trade", "trade_date", 0),
    ("st_list", "trade_date", 0),
)
# C 族：静态清单（sync_config id → 可读名）；id 必须与 sync_config 实际一致
FAMILY_C: dict[str, str] = {
    "astock_list": "A股列表",
    "listed_fund_list": "场内基金列表",
    "cb_basic": "转债基础",
    "trade_cal": "交易日历",
    "namechange_sync": "改名历史",
    "concept_sync": "概念分类",
}
# 批 146：按 id 分档——trade_cal 年度刷新（1 月 1 日，init-seed '0 9 1 1 *'）7 天阈值
# 必误伤（数据到 2026-12-31 齐全、10-05 success 仍报「77 天未成功」）；其余五键=周级。
C_STALE_DAYS: dict[str, int] = {
    "astock_list": 7,
    "listed_fund_list": 7,
    "cb_basic": 7,
    "trade_cal": 370,          # 年度任务（365+余量）
    "namechange_sync": 7,
    "concept_sync": 7,
}
D_STREAK_ALERT = 6
# 排序族权重：相等陈旧度时 A 族优先（日频、可自愈）
_FAMILY_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}


def date_col_of(pk_cols) -> str | None:
    """从主键列挑日期列（优先 trade_date > end_date > ann_date > 其它 *_date）。"""
    cols = list(pk_cols or [])
    for pref in ("trade_date", "end_date", "ann_date"):
        if pref in cols:
            return pref
    for c in cols:
        if c.endswith("_date"):
            return c
    return None


def symbol_col_of(pk_cols) -> str | None:
    """从主键列挑符号列（第一个非日期列——池表=ts_code，bar 表=symbol）。"""
    for c in list(pk_cols or []):
        if not c.endswith("_date"):
            return c
    return None


def family_of(table: str) -> str:
    if table in FAMILY_A:
        return "A"
    if table in FAMILY_D:
        return "D"
    return "D"


def last_dates(pg_table: str, date_col: str, sym_col: str, symbols=None) -> dict:
    """{symbol: MAX(date)}（只查给定标的；表/列不存在 ⇒ 空 dict，不抛）。"""
    try:
        with get_conn() as conn:
            if symbols:
                cur = conn.execute(
                    f"SELECT {sym_col}, MAX({date_col})::text FROM {pg_table} "
                    f"WHERE {sym_col} = ANY(%s) GROUP BY {sym_col}", (list(symbols),))
            else:
                cur = conn.execute(
                    f"SELECT {sym_col}, MAX({date_col})::text FROM {pg_table} "
                    f"GROUP BY {sym_col}")
            return {r[0]: r[1] for r in cur.fetchall() if r[0] and r[1]}
    except Exception as e:
        logger.warning("池对账：读 %s.%s 最新日期失败（跳过该表）: %s", pg_table, date_col, e)
        return {}


def _lag_days(last: str | None, tmax: str | None) -> int:
    """相对陈旧度（天）：以**该表自身最新日期**为基准，缺记录=极大值。

    为什么不用绝对日期（代码审 P0-3）：事件表整体停留在 2024 期，绝对日期排序会让它们
    每轮排最前、反复烧预算；用「与本表 tmax 的差」后，只有真正落后的标的高优先。
    """
    if not last:
        return 10 ** 6
    if not tmax:
        return 0
    try:
        d1 = datetime.strptime(last[:8], "%Y%m%d").date()
        d0 = datetime.strptime(tmax[:8], "%Y%m%d").date()
        return (d0 - d1).days
    except Exception:
        return 0


def stale_order(metas: dict, ts_codes: list) -> list:
    """时间盒起点：按「相对陈旧度降序」排，最陈旧先跑（无状态、重启安全）。

    键 = (陈旧度降序, 族权重, 表名, 标的)；无记录 (表,标的) 陈旧度=极大值 ⇒ 排最前。
    返回 [(ts_code, table)]。
    """
    rank: dict = {}
    for table, meta in metas.items():
        dc, sc = date_col_of(meta.get("pk")), symbol_col_of(meta.get("pk"))
        if not (dc and sc):
            continue
        seen = last_dates(meta["pg_table"], dc, sc, ts_codes)
        tmax = max(seen.values()) if seen else None
        for s in ts_codes:
            rank[(table, s)] = _lag_days(seen.get(s), tmax)
    return sorted(
        [(s, t) for t in metas for s in ts_codes],
        key=lambda p: (-rank.get((p[1], p[0]), 10 ** 6), _FAMILY_RANK[family_of(p[1])], p[1], p[0]))


def _traded_on(expected: str) -> set:
    """expected（YYYYMMDD）当天**有行情**的标的（vt 形态）；查不到 ⇒ 空集。

    用途：A 族只判「当天有行情 ⇒ 该有筹码数据」的标的——停牌/退市/次新当日无行是正常，
    否则会产出永不收敛的假缺口（代码审 P1）。
    """
    try:
        d = datetime.strptime(expected, "%Y%m%d").date()
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT symbol FROM bar_1D WHERE ts >= %s AND ts < %s",
                (d, d + timedelta(days=1)))
            return {r[0] for r in cur.fetchall() if r[0]}
    except Exception as e:
        logger.warning("池对账：读 bar_1D 当日标的失败（A 族退回全池判）: %s", e)
        return set()


def check_family_a(metas: dict, ts_codes: list, expected: str | None) -> list:
    """A 族：逐标的判「最近一条 < 上一交易日」——**只判当日有行情的标的**。"""
    findings = []
    if not expected:
        return findings
    traded_vt = _traded_on(expected)
    from src.data_platform.schema import to_vt_symbol
    for table in sorted(FAMILY_A):
        meta = metas.get(table)
        if not meta:
            continue
        dc, sc = date_col_of(meta.get("pk")), symbol_col_of(meta.get("pk"))
        if not (dc and sc):
            continue
        seen = last_dates(meta["pg_table"], dc, sc, ts_codes)
        if traded_vt:
            judged = [s for s in ts_codes if to_vt_symbol(s) in traded_vt]
        else:
            judged = list(ts_codes)
        missing = [s for s in judged if (seen.get(s) or "") < expected]
        if missing:
            findings.append(
                "A族 %s: %d/%d 标的落后于 %s（例：%s）" % (
                    table, len(missing), len(judged), expected, ", ".join(missing[:3])))
    return findings


def check_family_b() -> list:
    """B 族：全市场按日快照——最近交易日该表必须有行（不按标的判）。"""
    from .pool_data import _nearest_trade_day

    findings = []
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    expected = _nearest_trade_day(yesterday)
    if not expected:
        return findings
    for pg_table, dc, lag_days in FAMILY_B:
        # 批 147：lag 族 expected 再回退 lag 个交易日（T+1 发布节奏对齐）
        tbl_expected = expected
        for _ in range(lag_days):
            prev = _nearest_trade_day(
                (datetime.strptime(tbl_expected, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d"))
            if not prev or prev >= tbl_expected:
                break   # ntd 异常返空——不回退，宁可误报不静默。（注：ntd fail-open 返原值
                        # 形态下 prev=前一自然日字符串 < tbl_expected——本守卫不拦，按自然日
                        # 回退一次，属该函数陈旧守卫的既有容错语义，审 B2 P1-1 勘正）
            tbl_expected = prev
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {pg_table} WHERE {dc} >= %s", (tbl_expected,))
                n = int(cur.fetchone()[0])
        except Exception as e:
            logger.warning("池对账 B 族读 %s 失败: %s", pg_table, e)
            continue
        if n == 0:
            findings.append("B族 %s: 自 %s 起无任何行（全市场快照缺）" % (pg_table, tbl_expected))
    return findings


def check_family_c() -> list:
    """C 族：静态清单新鲜度（> 7 天未成功同步 ⇒ 报）。"""
    findings = []
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT id, last_status, last_sync_ts FROM sync_config WHERE id = ANY(%s)",
                (list(FAMILY_C),))
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("池对账 C 族读 sync_config 失败: %s", e)
        return findings
    now = datetime.now(timezone.utc)
    for sid, _status, ts in rows:
        name = FAMILY_C.get(sid, sid)
        if not ts:
            findings.append("C族 %s: 从未成功同步" % name)
            continue
        ref = now if ts.tzinfo else now.replace(tzinfo=None)
        age = (ref - ts).days
        _stale_days = C_STALE_DAYS.get(sid, 7)
        if age > _stale_days:
            findings.append("C族 %s: 已 %d 天未成功（阈值 %d）" % (name, age, _stale_days))
    return findings


def bump_d_streak(table: str, produced_rows: bool, errored: bool) -> int:
    """D 族接口健康计数：本轮 0 行且无错误 ⇒ +1，否则清零。返回当前 streak。"""
    key = "pool_d_streak:" + table
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key=%s", (key,))
            row = cur.fetchone()
            raw = str(row[0]) if row and row[0] is not None else "0"
            cur_v = int(raw) if raw.lstrip("-").isdigit() else 0
            new_v = 0 if (produced_rows or errored) else cur_v + 1
            conn.execute(
                "INSERT INTO system_config (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(new_v)))
            conn.commit()
            return new_v
    except Exception as e:
        logger.warning("池对账 D 族 streak 读写失败（%s）: %s", table, e)
        return 0


def check_family_d(per_table_saved: dict, errors: list) -> list:
    """D 族：接口健康——连续 N 轮全池 0 行且无错误 ⇒ 疑似上游断供。"""
    findings = []
    for table in sorted(FAMILY_D):
        if table not in per_table_saved:
            continue
        errored = any(table in e for e in errors)
        streak = bump_d_streak(table, per_table_saved.get(table, 0) > 0, errored)
        if streak >= D_STREAK_ALERT:
            findings.append("D族 %s: 连续 %d 轮全池 0 行（疑似上游断供）" % (table, streak))
    return findings


def reconcile(metas: dict, ts_codes: list, *, per_table_saved=None, errors=None,
              complete: bool = True) -> dict:
    """跑全族判定，返回 {a,b,c,d,findings}；任何族异常都被隔离成一条 finding。

    complete=False（本轮被时间盒截断）时**只跑只读族 A/B/C**——它们与截断无关；
    D 族的 streak 依赖「整轮全池 0 行」，截断轮计数无意义。
    """
    from .pool_data import _nearest_trade_day

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    expected = _nearest_trade_day(yesterday)
    out = {"a": [], "b": [], "c": [], "d": []}
    checks = [("a", lambda: check_family_a(metas, ts_codes, expected)),
              ("b", check_family_b),
              ("c", check_family_c)]
    if complete:
        checks.append(("d", lambda: check_family_d(per_table_saved or {}, errors or [])))
    for key, fn in checks:
        try:
            out[key] = fn()
        except Exception as e:
            logger.warning("池对账 %s 族判定异常（隔离）: %s", key, e)
            out[key] = ["%s 族判定异常: %s: %s" % (key, type(e).__name__, e)]
    out["findings"] = [x for k in ("a", "b", "c", "d") for x in out[k]]
    return out


def _throttle_ok(hours: int = 1) -> bool:
    """同一批 finding 最多每小时推一次（5 分钟一轮，否则一天 288 条告警）。"""
    import time as _t
    key = "pool_reconcile:last_notify"
    now = int(_t.time())
    try:
        with get_conn() as conn:
            cur = conn.execute("SELECT value FROM system_config WHERE key=%s", (key,))
            row = cur.fetchone()
            last = int(row[0]) if row and str(row[0]).isdigit() else 0
            if now - last < hours * 3600:
                return False
            conn.execute(
                "INSERT INTO system_config (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(now)))
            conn.commit()
            return True
    except Exception as e:
        logger.warning("池对账告警节流读写失败（本次不推送）: %s", e)
        return False


def notify_findings(findings: list) -> None:
    """对账发现 → 站内告警（1 小时节流；失败不阻）。"""
    if not findings or not _throttle_ok():
        return
    try:
        from src.alert_notify.notify import notify
        notify("warn", "data", "池数据对账发现 %d 项异常" % len(findings),
               "；".join(findings[:5]))
    except Exception as e:
        logger.warning("池对账告警推送失败: %s", e)
