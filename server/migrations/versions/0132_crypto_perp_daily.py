"""批 101：加密数据层第一步——币安 USDT-M 永续日线落 `bar_1d`。

**背景**：平台声称多市场（astock + crypto），骨架已备（`security_master` crypto 分支 /
`bar_4h` 等预留表 / `md_gateway` 币安·OKX / `broker` / 凭证 provider），但**数据层完全空白**：
`sync_config` 零 crypto 行、`bar_1d` 零 crypto symbol、8 张非日线 bar 表全空。

**原判「外部 gate＝币安/OKX API 未开通」已被 prod 实测推翻**（2026-10-06）：公开行情端点本就
无鉴权；真 gate 是**网络**——`data.binance.vision`（官方批量站，含 USDT-M 永续）在大陆 prod
**直连可达（200 / 0.3s，T+1）**，而 `fapi.binance.com`（永续实时）/ `www.okx.com` 等
**全部阻断**。故**非实时（历史 + T＋1）零 key 零代理可做**，实时腿需境外 relay（批 102）。
证据与设计见 `flow/方案/crypto数据层-立项设计-20261006.md`。

**本迁移只插两行配置**（expand-only：无 DDL、无 DELETE/RENAME）：
1. `sync_config.crypto_perp_daily`——provider=`binance`（新数据源 provider，0 密钥）、
   `trade_day_filter='none'`（crypto = **连续轴**，无交易日历）、`start_floor='2019-09-08'`
   （USDT-M 永续上线日；实测月包自 2020-01、更早回退日包）、`supports_backfill=true`
   （handler 真读 `backfill_from`）、`schedule='30 8 * * *'`。
   ⚠️ `data_sync_scheduler` 以**北京时区**解释 cron（见 `tasks.data_sync_scheduler` 的 TZ_CN），
   故 08:30 北京 = **00:30 UTC**——正是 UTC 昨日文件落盘之后（T+1 语义）。
2. `sync_kind_config.crypto_perp_daily`——归置 `(kind=bar_daily, sub_kind=perp)` → `bar_1d`；
   `sub_kind='perp'` 是**新增子类**（原 bar_daily 只有 stock/etf/convertible）。

**downgrade**：删这两行（对称）。行内无历史依赖——bar_1d 里的 crypto 行不随配置回滚删除
（数据与配置解耦；如需清数据另走人工）。

**写法**：用 `op.execute(<内联字面量 SQL>)` 而非 `op.get_bind().exec_driver_sql(<带参 SQL>)`——
后者在 `alembic upgrade --sql`（离线渲染）下炸 `MockConnection has no attribute exec_driver_sql`
（本用例区「离线渲染完整性」用例 N 实测抓到）。值全为静态字面量，内联无注入面。
（注：0129/0131 等历史迁移有同族写法，属既存债；已应用迁移不改，另批处理。）
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0132"
down_revision: Union[str, Sequence[str], None] = "0131"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYNC_ID = "crypto_perp_daily"


def upgrade() -> None:
    # 1) sync_config 行（幂等：ON CONFLICT DO NOTHING——尊重运行期改动，复跑不覆盖运维编辑）
    op.execute(
        "INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, "
        "schedule, enabled, trade_day_filter, provider, supports_backfill, start_floor, "
        "description) VALUES ("
        "'crypto_perp_daily', '加密永续日线', 'binance.klines', 'bar_1D', 'crypto', "
        "'incremental', '30 8 * * *', true, 'none', 'binance', true, '2019-09-08', "
        "'币安 USDT-M 永续日线（data.binance.vision 批量 ZIP，T+1）。全标的（排除 _YYMMDD 交割合约）。') "
        "ON CONFLICT (id) DO NOTHING")
    # 2) sync_kind_config 归置行（新子类 bar_daily/perp）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'crypto_perp_daily', 'bar_daily', 'perp', 'bar_1d', "
        "'{symbol,ts}', '{open,high,low,close,volume,amount}', '{}', 'incremental') "
        "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{_SYNC_ID}'")
    op.execute(f"DELETE FROM sync_config WHERE id = '{_SYNC_ID}'")
