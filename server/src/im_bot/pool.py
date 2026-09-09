"""IM pool —— 子进程管理器（批11C 方案 v2 双盲审 B-P0-1 修正）。

lark SDK `ws/client.py` 模块级全局 asyncio loop——单进程跑不了第二个 lark.ws.Client
（第二线程 run_until_complete 必 RuntimeError）。故 pool=**子进程管理器**：
主进程 30s 对账 enabled feishu bot → spawn `python -m src.feishu_bot.ws_client {bid}`
（现入口零改动，backfill_from_env 天然保留）/terminate 停用。单 bot 隔离：
坏凭证 bot 子进程退避重试不传染平台 bot（B-P0-2）。

对账白名单键集 {id, enabled, credentials_encrypted}（B-P1-5）：改名/改语言等
不动连接；凭证变更必触发（旧连接 token 不换）。

systemd quant-im-pool@quant.service（Restart=always 仅对 pool 主进程——主进程
只有对账循环，重启即全量重拉，安全）。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [im-pool] %(levelname)s %(message)s")
logger = logging.getLogger("im_pool")

POLL_S = 30
_BACKOFF_BASE = 60     # 坏 bot 子进程重启退避（秒，指数×2 封顶 10min）
_BACKOFF_MAX = 600


def _desired() -> dict[int, str] | None:
    """对账目标：enabled feishu bot 的 {bid: credentials_encrypted 指纹}。
    DB 失败返回 **None**（≠{}——代码盲审 A-P1-1/B-P0-1：{} 会被当"无目标"terminate 全部子进程，
    一次 DB 抖动/迁移持锁窗=全平台断连；None=本周期跳过对账不动现状）。"""
    try:
        from src.data_platform.db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, COALESCE(credentials_encrypted,'') FROM im_bot_config "
                "WHERE enabled AND provider='feishu'").fetchall()
        return {r[0]: str(r[1]) for r in rows}
    except Exception as e:
        logger.warning("对账查询失败（本周期跳过，不动现状）: %s", e)
        return None


class _Child:
    def __init__(self, bid: int):
        self.bid = bid
        self.proc: subprocess.Popen | None = None
        self.fingerprint = ""
        self.next_start = 0.0     # 退避到期时刻（monotonic）
        self.backoff = _BACKOFF_BASE

    def note_healthy(self) -> None:
        """子进程健康跑过一整个对账周期（≥POLL_S）→ 退避重置（B-P2-2：长跑后一次崩溃不该按累加倍数罚）。"""
        if self.proc and self.proc.poll() is None:
            self.backoff = _BACKOFF_BASE

    def ensure(self, fingerprint: str) -> None:
        """对账单 bot：起/换/停。凭证指纹变化=terminate 重起（换 token）。"""
        if self.proc and self.proc.poll() is None:
            if fingerprint != self.fingerprint:
                logger.info("bot %s 凭证变更，重启子进程", self.bid)
                self._stop()
            else:
                return
        now = time.monotonic()
        if now < self.next_start:
            return   # 退避窗内（坏 bot 不打飞书 API——B-P0-2）
        self.fingerprint = fingerprint
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "src.feishu_bot.ws_client", str(self.bid)])
        logger.info("bot %s 子进程起 pid=%s", self.bid, self.proc.pid)

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def reap(self) -> None:
        """退出子进程记退避（崩溃=凭证坏/网络断，不打满重试）。"""
        if self.proc and self.proc.poll() is not None:
            rc = self.proc.returncode
            logger.warning("bot %s 子进程退出 rc=%s，退避 %ss 后重试", self.bid, rc, self.backoff)
            self.next_start = time.monotonic() + self.backoff
            self.backoff = min(self.backoff * 2, _BACKOFF_MAX)
            self.proc = None


def main() -> None:
    children: dict[int, _Child] = {}
    logger.info("IM pool 起（30s 对账，子进程模型）")
    while True:
        time.sleep(POLL_S)
        try:
            want = _desired()
            if want is None:
                continue   # DB 失败：跳过本周期（fail-safe≠全 terminate——代码盲审 P0 修）
            # 停掉不再 desired 的
            for bid in list(children):
                if bid not in want:
                    logger.info("bot %s 停用/删除，terminate", bid)
                    children[bid]._stop()
                    del children[bid]
            # 起/换 desired 的
            for bid, fp in want.items():
                ch = children.get(bid) or children.setdefault(bid, _Child(bid))
                ch.reap()          # 退出者记退避
                ch.ensure(fp)
                ch.note_healthy()  # 存活满一周期者重置退避
        except Exception as e:
            # 主循环兜底（B-P2-1）：spawn OSError 等不传染主进程（Restart=always 会清 cgroup 全部子进程）
            logger.warning("对账周期异常（跳过本周期）: %s", e)


if __name__ == "__main__":
    main()
