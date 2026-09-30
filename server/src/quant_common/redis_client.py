"""Valkey 业务库 client 单源工厂（P2-5 后续收敛，2026-09-30）。

URL 单源在 `config.valkey_url()`；本模块把「建 client」也单源化——原 32 处调用点
各自 `Redis.from_url(valkey_url(), ...)`，样板是「URL 收敛后剩下的最后一层拷贝」。

设计要点：
- **透传不缓存**：内部就是 `redis.Redis.from_url(valkey_url(), **kw)`，kwargs 缺省
  对齐 redis-py 原生默认（decode_responses=False / 不设超时），各调用点的超时/解码
  语义显式传参逐点保留（监控 1s、任务 3s、批量读 5s、下单主路径 2s fail-closed 各有其义）。
  不做池缓存是**有意的**：① 语义保真优先——from_url 每次原样执行，行为与收敛前逐点等价；
  ② 测试 seam 兼容——全仓测试以 `patch("redis.Redis.from_url", ...)` 注入 fake client，
  工厂透传即兼容（若引入池缓存，fake 注入点即断，30+ 测试 mock 面重写，收益仅省
  十几条空闲连接，不值）。
- fork 安全与收敛前等价：client 本就不跨 fork 共享连接（prefork 子进程各自建）。

守门：`tests/test_valkey_url_single_source.py` 禁 `from_url(valkey_url(...))`
再散落（业务库 client 一律走本工厂）。
"""
from __future__ import annotations

import redis

from .config import valkey_url


def business_redis(*, socket_timeout: float | None = None,
                   socket_connect_timeout: float | None = None,
                   decode_responses: bool = False) -> redis.Redis:
    """db0 业务库 client（URL/样板单源；kwargs 透传，语义由调用点显式持有）。"""
    kw: dict = {}
    if socket_timeout is not None:
        kw["socket_timeout"] = socket_timeout
    if socket_connect_timeout is not None:
        kw["socket_connect_timeout"] = socket_connect_timeout
    return redis.Redis.from_url(valkey_url(), decode_responses=decode_responses, **kw)
