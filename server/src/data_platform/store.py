"""批 59·M4：存储层（dataset_version 读写 + 版本冻结骨架，29 号 §七 / 28 §7.3）。

dataset_version 语义：表级版本（每次数据变更 +1，如批 58b 竞价条并入 bar_minute 1→2），
行级列标记每行版本。cur_version=当前表级版本；frozen_or_current=冻结窗口（重建期）读旧版本。
本批只做骨架：版本号存 system_config 键，冻结窗口由 M5/数据变更批设 frozen 键。
"""
from __future__ import annotations

import logging

from .db import get_conn

logger = logging.getLogger("data_platform.store")


def _version_key(kind: str) -> str:
    return f"dataset_version:{kind}"


def _frozen_key(kind: str) -> str:
    return f"dataset_version_frozen:{kind}"


class Store:
    """本地 PG 仓的版本语义（28 §7.3）。"""

    def cur_version(self, kind: str) -> int:
        """读表级版本（system_config 键 dataset_version:{kind}，缺省 1；读失败=1）。

        ⚠️ 版本真源=列 DEFAULT（批 58b 已把 bar_minute 推到 2）；本键是读侧真源、缺省 1 与
        DEFAULT 2 存在不一致——数据变更批推进版本时须同步 seed 本键（挂账：统一版本真源）。
        """
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT value FROM system_config WHERE key=%s", (_version_key(kind),))
                r = cur.fetchone()
            return int(r[0]) if r else 1
        except Exception:
            return 1

    def frozen_or_current(self, kind: str) -> int:
        """冻结窗口读旧版本（dataset_version_frozen:{kind}），无冻结返回 cur_version。"""
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    "SELECT value FROM system_config WHERE key=%s", (_frozen_key(kind),))
                r = cur.fetchone()
            return int(r[0]) if r else self.cur_version(kind)
        except Exception:
            return self.cur_version(kind)

    def save(self, frame, version: int | None = None) -> int:
        """落仓：save_bars（11 字段，dataset_version 走列 DEFAULT——当前版本由迁移/数据变更批推进）。

        不显式写 dataset_version：①显式写会覆盖列 DEFAULT（58b 已把 bar_minute 推到 2，cur_version
        读 system_config 键缺省 1 会回退成 1）；②ON CONFLICT 跳过的存量行不该被统一回写当前版本
        （版本推进由迁移/数据变更批负责）。version 参数保留（frozen_or_current 语义，M5 版本冻结接）。
        """
        from .db import save_bars
        return save_bars(frame.freq, list(frame.rows))
