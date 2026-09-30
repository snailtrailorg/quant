"""Valkey client 单源工厂（P2-5 后续收敛，2026-09-30）。

URL 单源在 `config.valkey_url()` / `config.feishu_valkey_url()`；本模块把「建 client」
也单源化——原 32 处调用点各自 `Redis.from_url(valkey_url(), ...)`，样板是「URL 收敛后
剩下的最后一层拷贝」。

两个工厂（各读各库，语义不互相跟随）：
- `business_redis()`  → db0 业务库（熔断/JWT 黑名单/锁/进度/hub 流等）
- `feishu_redis()`    → db4 飞书长连接库（P0-3 独立变量；**不得**用业务工厂代替，
  否则 P0-3 的隔离失效复发）

设计要点：
- **透传不缓存**：内部就是 `redis.Redis.from_url(<单源 URL>, **kw)`，kwargs 缺省
  对齐 redis-py 原生默认（decode_responses=False / 不设超时），各调用点的超时/解码
  语义显式传参逐点保留（监控 1s、任务 3s、批量读 5s、下单主路径 2s fail-closed 各有其义）。
  不做池缓存是**有意的**：① 语义保真优先——from_url 每次原样执行，行为与收敛前逐点等价；
  ② 测试 seam 兼容——全仓测试以 `patch("redis.Redis.from_url", ...)` 注入 fake client，
  工厂透传即兼容（若引入池缓存，fake 注入点即断，30+ 测试 mock 面重写，收益仅省
  十几条空闲连接，不值）。
- fork 安全与收敛前等价：client 本就不跨 fork 共享连接（prefork 子进程各自建）。

守门：`tests/test_valkey_url_single_source.py` 禁**任何** redis `from_url(` 直调散落
（白名单仅本模块 + `web_api/redis_pool` 池本体）——「先算值再传」的间接写法
（`U = feishu_valkey_url(); from_url(U)`）同样在禁列，不靠名字匹配取巧。
"""
from __future__ import annotations

import redis

from .config import feishu_valkey_url, valkey_url


def _client(url: str, *, socket_timeout: float | None,
            socket_connect_timeout: float | None,
            decode_responses: bool) -> redis.Redis:
    kw: dict = {}
    if socket_timeout is not None:
        kw["socket_timeout"] = socket_timeout
    if socket_connect_timeout is not None:
        kw["socket_connect_timeout"] = socket_connect_timeout
    return redis.Redis.from_url(url, decode_responses=decode_responses, **kw)


def business_redis(*, socket_timeout: float | None = None,
                   socket_connect_timeout: float | None = None,
                   decode_responses: bool = False) -> redis.Redis:
    """db0 业务库 client（URL/样板单源；kwargs 透传，语义由调用点显式持有）。"""
    return _client(valkey_url(), socket_timeout=socket_timeout,
                   socket_connect_timeout=socket_connect_timeout,
                   decode_responses=decode_responses)


def feishu_redis(*, socket_timeout: float | None = None,
                 socket_connect_timeout: float | None = None,
                 decode_responses: bool = False) -> redis.Redis:
    """db4 飞书长连接库 client（P0-3 隔离：读 FEISHU_VALKEY_URL，不跟随业务库）。"""
    return _client(feishu_valkey_url(), socket_timeout=socket_timeout,
                   socket_connect_timeout=socket_connect_timeout,
                   decode_responses=decode_responses)
