"""批 172d P3 · 会话号段租约（DB 单语句原子抢占）。

唯一域 = (account_id, provider, terminal_id)（D32 §9.5）：
- TD 每任务一个会话：terminal_id = (tid-1)%98+2（2..99）
- MD 每账号一个会话：账号行预留号（当前域 [1,1]）
- 心跳 15s / TTL 45s；renew 与 release 都带 holder 条件（防旧进程删新主）

本模块只做租约原语：失败抛专用异常，**不抛 SystemExit**——退出码映射在入口层。
legacy 路径不调用本模块（impl='legacy' 走旧进程模型）。
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TTL_SECONDS = 45
TD_TERMINAL_RANGE = (2, 99)
MD_TERMINAL_RANGE = (1, 1)


class SessionBusy(Exception):
    """有主（心跳新鲜）→ 入口映射 EX_TEMPFAIL(75)。"""


class SessionConfigError(Exception):
    """terminal_id 越界/号段耗尽/配置非法 → 入口映射 EX_CONFIG(78)。"""


class SessionUnavailable(Exception):
    """DB 不可用 → 入口映射 EX_TEMPFAIL(75)，退避重试；绝不 fail-open 裸跑。"""


class SessionLost(Exception):
    """renew 返回 0 行 = 被抢 → 立即断开退出。"""


@dataclass(frozen=True)
class SessionSlot:
    account_id: int
    provider: str
    role: str            # md | td
    terminal_id: int
    order_prefix: str
    window_open: bool
    tid: int
    holder: str
    boot_epoch: int


def _default_holder() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _terminal_for(role: str, tid: int, terminal_id: int | None) -> int:
    lo, hi = TD_TERMINAL_RANGE if role == "td" else MD_TERMINAL_RANGE
    if role == "td":
        candidate = terminal_id if terminal_id is not None else (tid - 1) % 98 + 2
    else:
        if terminal_id is None:
            raise SessionConfigError("MD 会话必须显式给 terminal_id（账号预留号）")
        candidate = terminal_id
    if not lo <= int(candidate) <= hi:
        raise SessionConfigError(f"{role} terminal_id={candidate} 越界（{lo}..{hi}）")
    return int(candidate)


class SessionSlotAllocator:
    @classmethod
    def acquire(cls, account_id: int, provider: str, role: str, tid: int,
                boot_epoch: int, window_open: bool = False,
                terminal_id: int | None = None, holder: str | None = None) -> SessionSlot:
        terminal = _terminal_for(role, tid, terminal_id)
        holder = holder or _default_holder()
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "INSERT INTO provider_session_slot "
                    "(account_id, provider, terminal_id, holder, boot_epoch, heartbeat) "
                    "VALUES (%s,%s,%s,%s,%s,now()) "
                    "ON CONFLICT (account_id, provider, terminal_id) DO UPDATE "
                    "SET holder=EXCLUDED.holder, boot_epoch=EXCLUDED.boot_epoch, heartbeat=now() "
                    "WHERE provider_session_slot.heartbeat < now() - interval '45 seconds' "
                    "RETURNING holder",
                    (account_id, provider, terminal, holder, boot_epoch)).fetchone()
                conn.commit()
        except Exception as e:   # noqa: BLE001 —— DB 不可用必须退避，禁止裸跑
            raise SessionUnavailable(f"session slot DB 不可用: {e}") from e
        if not row or row[0] != holder:
            raise SessionBusy(f"terminal_id={terminal} 有主（account={account_id} provider={provider}）")
        return SessionSlot(
            account_id=account_id, provider=provider, role=role,
            terminal_id=terminal, order_prefix=f"t{tid}:e{boot_epoch}:",
            window_open=window_open, tid=tid, holder=holder, boot_epoch=boot_epoch,
        )

    @classmethod
    def renew(cls, slot: SessionSlot) -> None:
        """续租；0 行=被抢（SessionLost）；异常=DB 错（SessionUnavailable，调用方重试）。"""
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "UPDATE provider_session_slot SET heartbeat=now() "
                    "WHERE account_id=%s AND provider=%s AND terminal_id=%s AND holder=%s "
                    "AND heartbeat > now() - interval '45 seconds' RETURNING 1",
                    (slot.account_id, slot.provider, slot.terminal_id, slot.holder)).fetchone()
                conn.commit()
        except Exception as e:   # noqa: BLE001
            raise SessionUnavailable(f"session slot 续租失败: {e}") from e
        if not row:
            raise SessionLost(f"租约已被抢（terminal_id={slot.terminal_id}）")

    @classmethod
    def release(cls, slot: SessionSlot) -> None:
        """释放（带 holder 条件：旧进程不得删抢到者的行）。失败只告警。"""
        try:
            from src.data_platform.db import get_conn
            with get_conn() as conn:
                conn.execute(
                    "DELETE FROM provider_session_slot "
                    "WHERE account_id=%s AND provider=%s AND terminal_id=%s AND holder=%s",
                    (slot.account_id, slot.provider, slot.terminal_id, slot.holder))
                conn.commit()
        except Exception as e:   # noqa: BLE001 —— 退出路径不因清理失败阻断
            logger.warning("session slot 释放失败（忽略）: %s", e)
