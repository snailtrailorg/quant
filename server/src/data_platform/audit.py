"""审计日志（从 web_api/auth.py 下沉，2026-08-19 P 审——纯 DB INSERT 属数据层；
web_api.feishu_bot 曾因此反向 import 顶层）。"""
from __future__ import annotations

import logging

_logger = logging.getLogger("data_platform.audit")


def _no_nul(s: str) -> str:
    """剥 NUL（PG text 禁 0x00——健壮性测试实证：%00 路径参数经 audit_log 落库
    直接 psycopg.DataError 500。审计是旁路记录，永不炸业务；剥后保留可见性）。"""
    return s.replace("\x00", "␀") if isinstance(s, str) else s


def audit_log(actor: str, action: str, target: str = "", detail: str = "",
              old_value: str = "", new_value: str = ""):
    """写审计日志（含新旧值对比）。审计失败不炸业务（fail-soft：warning + 放行）。"""
    from .db import get_conn
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (actor, action, target, detail, old_value, new_value) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (_no_nul(actor), _no_nul(action), _no_nul(target),
                 _no_nul(detail), _no_nul(old_value), _no_nul(new_value)),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        # 审计是旁路：DB 故障/非法字节不阻断主流程（主操作已发生或将继续；
        # 宁丢一条审计不炸一个请求——与「判定层不依赖数据模块生命周期」同向）
        _logger.warning("audit_log 写入失败（已放行主流程）: %s", e)
