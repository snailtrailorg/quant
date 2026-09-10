"""IM pool 子进程入口包（批13）——各平台长连接 runner。

pool 对账按 provider 映射 spawn：feishu→src.feishu_bot.ws_client（存量不动）；
dingtalk/wecom→本包（凭证预检 fail-fast + 官方/自实现长连接）。
"""
