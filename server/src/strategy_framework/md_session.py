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


def is_trading_day(today: datetime | None = None) -> bool:
    """交易日（数据中台日历优先；缺失保守按工作日）。"""
    today = today or datetime.now()
    try:
        from src.data_platform import platform
        return platform.is_trading_day(today.date())
    except Exception:
        return today.weekday() < 5


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

    RENEW_HM = (9, 10)          # 续航时刻（交易日，开盘前）
    BACKOFF_START = 30.0        # 反应式重登起始退避
    BACKOFF_CAP = 300.0         # 封顶 5min

    def __init__(self, md_api):
        self._md = md_api
        self._renewed_date = None       # 定时续航当日去重
        self._last_retry_ts = 0.0       # 上次反应式重登时刻
        self._backoff = self.BACKOFF_START

    def _logout_quietly(self) -> None:
        """尽力优雅登出：半开连接（TCP 在但会话失效）上直接 login() 必 EISCONN +
        服务端 "user already exists"（2026-08-25 runner 实锤）--先 logout 通知服务端
        释放会话槽再重登。logout 签名跨版本不稳（部分带 session 参数），失败不致命
        （状态归位即可，下一轮重试再清）。
        """
        md = self._md
        try:
            logout = getattr(md, "logout", None)
            if callable(logout):
                try:
                    logout()
                except TypeError:
                    logout(0)   # 旧版签名带 session 参数
            md.connect_status = False
            md.login_status = False
        except Exception as e:
            logger.debug("MD logout 清场未生效: %s", e)

    def renew(self) -> bool:
        """重登 = SDK 认可路径 login_server()（onDisconnected 内部同款调用）。

        不 exit 不重建 API 对象：连接级问题进程内闭环（分层规则：退出只属于进程域故障）。
        2026-08-25 修订：①已登录态先 logout 清场（半开 socket 上 login 必 OS:106）；
        ②quote login 同步返回，login_status 即结果，未确认时再补一发 logout 给下一轮
        清出服务端会话槽。每次发起后退避翻倍（封顶），on_recovered 清零。
        """
        if self._md is None:
            return False
        try:
            if getattr(self._md, "connect_status", False):
                self._logout_quietly()
            self._md.login_server()
            ok = bool(getattr(self._md, "login_status", False))
            if not ok:
                self._logout_quietly()
            self._last_retry_ts = time.time()
            self._backoff = min(self._backoff * 2, self.BACKOFF_CAP)
            logger.info("MD 会话重登已发起（定时续航或反应式，%s，下次退避 %.0fs）",
                        "已确认" if ok else "未确认", self._backoff)
            return True
        except Exception as e:
            logger.warning("MD 重登发起失败: %s", e)
            return False

    def schedule_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self._renewed_date == now.date():
            return False
        if not is_trading_day(now):
            return False
        if (now.hour, now.minute) < self.RENEW_HM:
            return False
        self._renewed_date = now.date()
        return True

    def retry_ready(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        if not self._last_retry_ts:
            return True  # 从未重试过：首次症状立刻触发，不等待
        return now - self._last_retry_ts >= self._backoff

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
