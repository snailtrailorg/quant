"""批 102a：代理配置体系（全局池 + 按消费方绑定）——expand-only（只建两张新表）。

**为什么落 DB 而不落 .env / 代码**（威廉姆 2026-10-06 裁决的工程化，设计 §3.2）：
- dev/staging/prod **各自独立 DB** ⇒ 「同一市场不同服务器不同用法」天然成立（同一份代码读各自库）；
- 与交易凭证（interfaces）同层同模式——本仓已有「凭证落库 + API 掩码」的先例；
- 可 UI 管理、可审计、有权限门。

**两表语义**：
- `proxy_config`   = 全局代理池（DSN + 备注 + 启停）。`url` 的 scheme 词表＝
  `{socks5h, socks5, http, https}`（威廉姆裁定**不锁死 SOCKS**）。校验在应用层
  （`data_platform/proxy.py::validate_url`），DDL 只约束非空——**不武断封杀 `socks5://`**
  （本地 DNS 可用的内网场景合法），由 UI 提示 + probe 实测把关。
- `proxy_binding`  = 按消费方绑定，两个独立选项（威廉姆原话）：**是否启用** + **选池中哪一个**。
  enable 与 select 分立是刻意的（UI 上开关与下拉各自独立，换代理不动开关）。
  `endpoint_override` 把各 provider 的服务端点配置化（NULL＝用代码缺省，缺省保证零配置可跑）。

**`consumer` 键空间＝provider 名**：数据面（`binance`/`okx`/`tushare`）与通道面
（`binance_perp`/`okx_perp`）**同名空间共用一张绑定表**——通道面未来（实时腿）免费复用。

**seed 原则**：本迁移**不 seed 任何代理地址**（AWS 那台的 DSN 属运维态、非代码态）；
表建好后经 UI/API 配置，dev/prod 各配各的。

**DNS 语义（真正的硬约束，落在 §3.4 而非 DDL）**：`socks5h` 与 `http(s)` 代理**都在代理侧
解析目标域名**（http 代理对 https 走 `CONNECT host:port`、对 http 转发绝对 URI），
**唯 `socks5://` 本地解析** ⇒ 遇被污染域名（prod 上 OKX → `169.254.0.2`）必死。
故 prod 上 OKX 必须用 `socks5h://` 或 `http(s)://`。

**写法**：`op.execute(<内联字面量 SQL>)`（`--sql` 离线渲染安全，同 0132/0133/0134）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0135"
down_revision: Union[str, Sequence[str], None] = "0134"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 全局代理池
    op.execute(
        "CREATE TABLE IF NOT EXISTS proxy_config ("
        "  name       TEXT PRIMARY KEY,"
        "  url        TEXT NOT NULL,"
        "  notes      TEXT,"
        "  enabled    BOOLEAN NOT NULL DEFAULT true,"
        "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")")
    # 按消费方绑定（enable + select 两选项；endpoint_override 端点配置化）
    op.execute(
        "CREATE TABLE IF NOT EXISTS proxy_binding ("
        "  consumer          TEXT PRIMARY KEY,"
        "  proxy_enabled     BOOLEAN NOT NULL DEFAULT false,"
        "  proxy_name        TEXT REFERENCES proxy_config(name) ON DELETE SET NULL,"
        "  endpoint_override TEXT,"
        "  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),"
        "  CONSTRAINT ck_proxy_binding_name CHECK (NOT proxy_enabled OR proxy_name IS NOT NULL)"
        ")")


def downgrade() -> None:
    # 先删引用方（proxy_binding 有 FK 指向 proxy_config）
    op.execute("DROP TABLE IF EXISTS proxy_binding")
    op.execute("DROP TABLE IF EXISTS proxy_config")
