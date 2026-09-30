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
"""
from __future__ import annotations

import os

DEFAULT_VALKEY_URL = "redis://127.0.0.1:6379/0"
DEFAULT_FEISHU_VALKEY_URL = "redis://127.0.0.1:6379/4"


def valkey_url() -> str:
    """业务库（db0）连接串单源。"""
    return os.environ.get("VALKEY_URL") or DEFAULT_VALKEY_URL


def feishu_valkey_url() -> str:
    """飞书长连接库（db4）连接串单源。"""
    return os.environ.get("FEISHU_VALKEY_URL") or DEFAULT_FEISHU_VALKEY_URL
