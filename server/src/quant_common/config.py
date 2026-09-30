"""连接配置单源（P2-5 收敛，2026-09-30）。

历史：VALKEY_URL 默认连接串曾拷贝 34 处（审计 2026-09-29 单一真源扫描 D 段）——
改一次默认值要动 N 处，漏一处即静默连错实例（P0-3 飞书池隔离失效就是已发生后果）。
现收敛到本模块两个取数函数；全仓**禁止**再写
`os.environ.get("VALKEY_URL", "redis://...")` 字面量（守门测试
server/tests/test_valkey_url_single_source.py 锁死）。

语义：
- VALKEY_URL       → db0 业务库（熔断/JWT 黑名单/锁/进度/hub 流等）
- FEISHU_VALKEY_URL → db4 飞书长连接库（P0-3 独立变量，与业务库隔离；
  未显式配置时独立落 db4——不跟随 VALKEY_URL）

**空串语义（2026-09-30 裁定，专家复核项）**：取数用严格 `.get(key, default)` 而非
`os.environ.get(key) or default`——**空串不等于未配置**。理由：本模块的唯一目的是
「消灭静默连错实例」，而 `or` 会把 `VALKEY_URL=`（空串，配置错误）静默降级成
127.0.0.1 默认实例：服务器上若有历史本地 Valkey，即无声连到另一个实例——正是 P0-3
那一类失败。空串应让建连当场暴露（`from_url("")` 立即抛错），而不是换个地方悄悄成功。
（仓内 `scheduler/app.py` 等处仍用 `or` 读 CELERY_* ——那是历史写法，非本模块口径；
如需全仓统一为「空串=未配置」，应作为独立决策整体裁定，不要只在单源处改。）
"""
from __future__ import annotations

import os

DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/0"
DEFAULT_FEISHU_VALKEY_URL = "redis://127.0.0.1:6379/4"


def valkey_url() -> str:
    """业务库（db0）连接串单源。空串按配置错误原样返回（不回落默认实例，见模块 docstring）。"""
    return os.environ.get("VALKEY_URL", DEFAULT_VALKEY_URL)


def feishu_valkey_url() -> str:
    """飞书长连接库（db4）连接串单源。空串按配置错误原样返回（不回落默认实例）。"""
    return os.environ.get("FEISHU_VALKEY_URL", DEFAULT_FEISHU_VALKEY_URL)
