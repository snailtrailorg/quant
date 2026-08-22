"""Web 后端共享 Valkey 连接池（单一实例）。

盲审遗留收敛（2026-08-22）：P4 拆分后 routes/ 6 份 from_url 拷贝中 4 份为死代码
（定义后从未使用，已删），实际在用的两处（sync 端点 db0 / im_bots 飞书长连接 db4）
收敛到此。池为模块级单例，进程内共享；新建连接池而不复用才是连接泄漏源。
"""

import os

import redis

redis_pool = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/0"),
    decode_responses=True,
)

# 飞书长连接专用库（db4）：ws_client 心跳/重连状态等，与业务库隔离
feishu_redis_pool = redis.ConnectionPool.from_url(
    os.environ.get("VALKEY_URL", "redis://127.0.0.1:6379/4"),
    decode_responses=True,
)


def redis_client() -> redis.Redis:
    """db0 业务库客户端（池共享）。"""
    return redis.Redis(connection_pool=redis_pool)


def feishu_redis_client() -> redis.Redis:
    """db4 飞书长连接库客户端（池共享）。"""
    return redis.Redis(connection_pool=feishu_redis_pool)
