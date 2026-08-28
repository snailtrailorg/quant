"""行情会话生命周期契约（L2 会话层，2026-08-24 韧性分层模型）。

运行时韧性分层（docs/architecture/12-实盘稳定性设计.md §运行时韧性分层模型）：
- L1 机器层（systemd）：只管进程死活，永远不被数据流症状触达；
- L2 会话层（本模块）：连接/会话/订阅的进程内自愈--已知周期失效用**定时续航**，
  未知失效用**反应式重登**（有界退避，无上限重试，无退出路径）；
- L3 意图层（scheduler.sa4_reconciler）：DB 期望状态 vs systemd 实际状态的调和。

引擎（md_hub/strategy_runner）只依赖 MdSessionBase 契约；XTP 的日切时刻、重登手法
等平台领域知识全封在子类（接新平台=实现子类，框架零改动）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger("md_session")

# ——— 注册 quant_common.session 的市场配置回调（层 0 底座不直接依赖 data_platform）———
def _market_config_provider(market: str) -> dict | None:
    """从 market_session 表加载市场配置供 quant_common.session.in_session 消费。

    calendar_dates 在此解析（从 DB 取 Tushare 交易日历），quant_common 只拿纯集合作判定。
    """
    import json
    from src.quant_common.session import _load_market_config  # trigger cache init
    from src.data_platform.db import get_conn, get_trade_calendar
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT calendar, session_rules, tz FROM market_session WHERE name=%s",
                (market,))
            row = cur.fetchone()
        if row:
            cal, rules, tz = row
            rules = json.loads(rules) if isinstance(rules, str) else rules
            cfg = {"calendar": cal, "session_rules": rules, "tz": tz, "calendar_dates": None}
            # 将 tushare_sse/szse 解析为 dates 集（quant_common 不碰 DB 的代价在此）
            if cal in ("tushare_sse", "tushare_szse"):
                from datetime import date as _d
                cal_dates = get_trade_calendar(_d.today().year)
                if cal_dates:
                    cfg["calendar_dates"] = set(cal_dates)
            return cfg
    except Exception:
        pass
    return None

try:
    from src.quant_common.session import set_config_provider
    set_config_provider(_market_config_provider)
except Exception:
    pass  # 测试/早期导入窗口静默


_TD_CACHE: dict = {}   # date -> bool（D2 按日缓存：schedule_due 每步都在打 DB，一处修）


# ——— 每日连接窗（P2 批 2026-08-28，双盲审定型）———
# 纯函数供 MD(supervisor)与 TD(worker)两处轮询（A 补充点②）；main 层读配置传纯参。
# 窗沿锚点：开=9:15 集合竞价（TD 最早挂单/竞价 tick）−lead；关=15:00 收盘+lag
# （15:01 收盘根 flush 15:01:05-30 先于关沿 ✓）。任一键=0 → 禁用（永久连接，旧行为）。
_XTP_OPEN_ANCHOR_HM = (9, 15)      # 集合竞价开始
_XTP_CLOSE_ANCHOR_HM = (15, 0)     # 收盘
_WINDOW_MAX_MIN = 600              # lead/lag 上限（10h），越界钳制


def _clamp_min(v, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return 0 if n <= 0 else min(n, _WINDOW_MAX_MIN)


def xtp_session_window_open(now: datetime, lead_min: int, lag_min: int,
                            trading_day: bool | None = None) -> bool:
    """XTP/A 股每日连接窗判定（纯函数，A/B 双盲审）。

    - lead/lag 任一 <=0：日窗禁用 → 恒 True（永久连接，旧行为逃生门）
    - 交易日由调用方传入（MD 侧 is_trading_day 的 weekday 回退在 A 股语境已是
      fail-open——交易日⊂工作日，读失败→当工作日→连，永不漏连；A 补充点①零改动裁定）
    - 窗内含 15:00 整分钟（收盘竞价回报尾窗），比 in_session 宽——勿互替（B-P2④）
    """
    if (int(lead_min) <= 0) or (int(lag_min) <= 0):
        return True
    if trading_day is False:
        return False
    # 真分钟算术（伪十进制 hm 减法跨小时会错：9:15-95min=7:40 而 915-95=820）
    cur = now.hour * 60 + now.minute
    oh, om = _XTP_OPEN_ANCHOR_HM
    ch, cm = _XTP_CLOSE_ANCHOR_HM
    open_min = (oh * 60 + om) - _clamp_min(lead_min, 10)
    close_min = (ch * 60 + cm) + _clamp_min(lag_min, 10)
    # 开沿含整分（9:05:00 即建连）、关沿排他（15:10:00 即属窗外=断开时刻，用户语义
    # 「15:10 logout 并关闭」——盲审 A-P2① 边界口径）
    return open_min <= cur < close_min


def _reset_td_cache() -> None:
    """清按日缓存（D2 坑③：测试钩子——mock 后缓存脏值=假绿假红，跨测试必重置）。"""
    _TD_CACHE.clear()


def is_trading_day(today: datetime | None = None) -> bool:
    """交易日（数据中台日历优先；缺失保守按工作日）。

    D2 按日缓存（批 4b，缓存下沉本体）：schedule_due 内部每步（hub 5s / direct 10s）
    都在裸打 DB——缓存进本体一处修，hub 消自身缓存、direct 消裸查。三坑规约：
    ①键=**参数的 date**（非 now().date()——schedule_due 显式传 now、测试传固定日期）；
    ②**只缓存 DB 成功读**——失败回退 weekday 值不缓存（假日撞 DB 抖动不得被当交易日
    冻结一天）；③测试用 ``_reset_td_cache()`` 清（防跨测试污染）。
    知情差异落档：日历盘中变更最迟次日生效；与 in_session 的 60s 配置缓存新鲜度不一致
    （hub 迁移前已是每日级，等价）。
    """
    today = today or datetime.now()
    key = today.date()
    cached = _TD_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from src.data_platform import platform
        v = bool(platform.is_trading_day(key))
        _TD_CACHE[key] = v
        return v
    except Exception:
        # 用户裁定（2026-08-28）：读不到日历=当自然日（每天都是交易日）——错误方向统一为
        # "白连无害"，绝不漏连。原 weekday<5 回退自身是故障源（时区/服务器时钟错→
        # weekday 判错日→漏连）；fail-open 到自然日后该故障点彻底消失。
        return True


def load_xtp_window_cfg(default_lead: int = 10, default_lag: int = 10) -> tuple[int, int]:
    """读 system_config 每日连接窗两键（P2 批 08-28；main 层启动读一次传纯参，改动重启生效）。

    `xtp_session_lead_min`/`xtp_session_lag_min`，默认 10/10；**任一 <=0 = 禁用日窗**
    （永久连接，旧行为逃生门）。DB 不可达用默认。lazy import 同 _market_config_provider
    先例（本模块允许 DB 边界，quant_common 层 0 保持纯净）。
    """
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            cur = conn.execute(
                "SELECT key, value FROM system_config WHERE key IN "
                "('xtp_session_lead_min', 'xtp_session_lag_min')")
            kv = dict(cur.fetchall())

        def _num(key: str, d: int) -> int:
            raw = kv.get(key)
            if raw is None or raw == "":
                return d
            try:
                return int(raw)
            except ValueError:
                return d

        return _num("xtp_session_lead_min", default_lead), _num("xtp_session_lag_min", default_lag)
    except Exception:
        return default_lead, default_lag


def zombie_session(sess_now: bool, sess_ticks: int, sess_enter_ts: float,
                   now: float, trading_day: bool, grace: float = 600.0) -> bool:
    """僵尸会话判定（2026-08-24 09:30-11:01 实锤）：平台日切（≈23:53）关行情连接后
    SDK 重登失败即永久静默--进程/心跳/连接态全正常但零数据。

    交易时段 + 交易日 + 零 tick 超宽限（默认 10min，避开竞价静默窗口）即判死。
    有过 tick 再断流（sess_ticks>0）不在此列--那可能是平台级故障，退出治不了
    （S6 原则：只告警不自杀），两分支失效模式不同故分开。
    """
    return bool(sess_now and trading_day and sess_ticks == 0
                and sess_enter_ts and now - sess_enter_ts > grace)


class MdSessionBase:
    """行情会话契约：定时续航 + 反应式重登。

    引擎主循环（10s 级）调用约定：
    1. ``schedule_due()`` 真 -> 调 ``renew()``（定时续航，当日一次）；
    2. 症状持续（零 tick 超宽限/断流）且 ``retry_ready()`` 真 -> 调 ``renew()``（反应式）；
    3. 数据恢复 -> 调 ``on_recovered()`` 清退避。

    订阅恢复不归本契约管：重登后新会话订阅为空，由引擎既有的周期性幂等重放
    （hub 每分钟全量重放 / runner 盘中每 60s 重订阅）在 ≤60s 内自动补齐。
    """

    def renew(self) -> bool:
        """换新会话。幂等；返回是否发起了重登动作。"""
        raise NotImplementedError

    def schedule_due(self, now: datetime | None = None) -> bool:
        """定时续航时刻已到（子类持有时刻表；内部记当日已续航防重复）。"""
        raise NotImplementedError

    def retry_ready(self, now: float | None = None) -> bool:
        """反应式重登的退避已到点（退避表内部持有）。"""
        raise NotImplementedError

    def on_recovered(self) -> None:
        """数据恢复：清退避计数（下次症状从头起算）。"""
        raise NotImplementedError


class XtpMdSession(MdSessionBase):
    """XTP 行情会话：交易日 09:10 定时续航 + 症状驱动反应式重登。

    背景（2026-08-24 实锤）：XTP 平台日切（≈23:53）丢弃行情会话，vnpy_xtp 的
    ``onDisconnected -> login_server`` 单次重登失败（如 user already exists）后
    无人再触发 = 永久静默；进程/心跳/连接态全部正常但零数据（僵尸会话）。
    - 定时续航：开盘前换新鲜会话，18 小时无效重试归零（09:10 < 竞价 09:15）；
    - 反应式：盘中症状兜底（续航漏掉的/盘中突发的），退避 30s 指数封顶 5min。
    """

    RENEW_HM = (9, 10)          # 续航窗口起（交易日，开盘前；lead 配置时由构造覆盖）
    RENEW_END_HM = (9, 30)      # 续航窗口止（开盘）——盘中启动的进程已有新鲜登录，续航只会
                                # 自杀式 churn（2026-08-25 14:05 实锤：14:05:05 登录成功 →
                                # 14:05:22 无谓 renew 把健康会话 logout，槽回收竞态又自盲 10min）
    _XTP_OPEN_HM = (9, 15)      # 窗开锚=集合竞价（与模块级 xtp_session_window_open 一致）
    _XTP_CLOSE_HM = (15, 0)
    BACKOFF_START = 30.0        # 反应式重登起始退避
    BACKOFF_CAP = 300.0         # 封顶 5min

    def __init__(self, md_api, lead_min: int = 0, lag_min: int = 0):
        """lead/lag>0 时启用每日连接窗（P2 批 08-28，双盲审定型）：

        - 窗开沿建连**不做独立 connect**——复用本类 schedule_due→renew→relogin 单原语
          （B-P0 互锁：_renewed_date 当日去重天然置位，双原语并发会重演 08-25 churn）；
          relogin 在 CREATED 态=直登（guard 已证），配合 main 侧 defer_login 启动即冷态可建。
        - 续航窗起随 lead 推导（9:15−lead），收盘后由 supervisor 调 close_for_window()
          （官方序列 Logout，窗关态本类的反应式腿由 in_session=False 天然灭+supervisor 窗闸双保险）。
        """
        self._md = md_api
        self._renewed_date = None       # 定时续航当日去重
        self._last_retry_ts = 0.0       # 上次反应式重登时刻
        self._backoff = self.BACKOFF_START
        self._lead = _clamp_min(lead_min, 10)
        self._lag = _clamp_min(lag_min, 10)
        if self._lead > 0:
            open_min = (self._XTP_OPEN_HM[0] * 60 + self._XTP_OPEN_HM[1]) - self._lead
            self.RENEW_HM = (open_min // 60, open_min % 60)   # 实例级覆盖（类默认留给无窗模式）

    def renew(self) -> bool:
        """重登 = 守卫官方序列 ``relogin()``（Logout→Login，批 1 移交 GuardedXtpMdApi）。

        MdSessionBase 契约不变：返回是否发起了重登动作；后端异常在此终结
        （L2 无退出路径）。每次发起后退避翻倍（封顶），on_recovered 清零。
        """
        if self._md is None:
            return False
        relogin = getattr(self._md, "relogin", None)
        if not callable(relogin):
            logger.warning("MD 后端无 relogin（应接 GuardedXtpMdApi），跳过重登")
            return False
        try:
            ok = bool(relogin())
            self._last_retry_ts = time.time()
            self._backoff = min(self._backoff * 2, self.BACKOFF_CAP)
            if not ok and self._renewed_date:
                # 双盲审 P1-2（2026-08-25）：定时续航未确认（开盘前槽回收竞态）时当日
                # 标记必须回滚，窗口内按退避重试——否则唯一兜底=反应式宽限（进沿+600s
                # ≈ 09:40），开盘最贵 10 分钟盲区。relogin 已带 -2 清场，紧接重试成功率高。
                self._renewed_date = None
            logger.info("MD 会话重登已发起（定时续航或反应式，%s，下次退避 %.0fs）",
                        "已确认" if ok else "未确认", self._backoff)
            return True
        except Exception as e:
            # 异常也计入退避：防后端持续抛错时无节奏重试
            self._last_retry_ts = time.time()
            self._backoff = min(self._backoff * 2, self.BACKOFF_CAP)
            logger.warning("MD 重登发起失败（%.0fs 后重试）: %s", self._backoff, e)
            return False

    def schedule_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self._renewed_date == now.date():
            return False
        if not is_trading_day(now):
            return False
        hm = (now.hour, now.minute)
        if hm < self.RENEW_HM or hm >= self.RENEW_END_HM:
            return False   # 窗口外不续航：盘中启动=已有新鲜登录，重登只会 churn
        # 双盲审 P1-2（2026-08-25）：未确认回滚后窗口内重试须按退避节奏（防 10s 紧循环）；
        # 首航 _last_retry_ts=0 即刻触发
        if not self.retry_ready():
            return False
        self._renewed_date = now.date()
        return True

    def retry_ready(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        if not self._last_retry_ts:
            return True  # 从未重试过：首次症状立刻触发，不等待
        return now - self._last_retry_ts >= self._backoff

    def logged_in(self) -> bool:
        """会话确认态（supervisor 窗开首航告警用）；非 guard 后端无 state 时恒 True。"""
        st = getattr(self._md, "state", None)
        return True if st is None else getattr(st, "name", "") == "LOGGED_IN"

    # ——— 每日连接窗（P2 批 08-28；supervisor 每 5s 轮询=定时器，非一次性判定）———

    def window_open(self, now: datetime | None = None) -> bool:
        """窗开判定（交易日读不到=当自然日，is_trading_day 本体 fail-open）。"""
        now = now or datetime.now()
        return xtp_session_window_open(now, self._lead, self._lag,
                                        trading_day=is_trading_day(now))

    def window_close_due(self, now: datetime | None = None) -> bool:
        """窗关沿（已登录且窗外 → 该挂起）。lead/lag=0 恒 False（禁用日窗不断开）。"""
        if self._lead <= 0 or self._lag <= 0:
            return False
        return not self.window_open(now)

    def close_for_window(self) -> bool:
        """窗关主动挂起=guard.suspend()（logout 清槽保持 CREATED，次日窗开沿
        relogin 直登）。非 guard 后端无 suspend → 返回 False 由 supervisor 告警。"""
        suspend = getattr(self._md, "suspend", None)
        if not callable(suspend):
            logger.warning("MD 后端无 suspend（应接 GuardedXtpMdApi），窗关断开跳过")
            return False
        try:
            suspend()
            logger.info("MD 会话窗关挂起（logout，保持 CREATED 待窗开直登）")
            return True
        except Exception as e:
            logger.warning("MD 窗关挂起失败: %s", e)
            return False

    def on_recovered(self) -> None:
        """数据恢复：清退避计数（下次症状从头起算）。

        _last_retry_ts 必须一并清零：不清则打屏守卫（``or self._last_retry_ts``）永真，
        「MD 数据恢复」日志随健康检查每轮刷屏（2026-08-25 hub 实测 5s/条）；清零后
        retry_ready 回到"首次症状立即触发"语义。
        """
        if self._backoff != self.BACKOFF_START or self._last_retry_ts:
            logger.info("MD 数据恢复，反应式退避清零")
        self._backoff = self.BACKOFF_START
        self._last_retry_ts = 0.0
