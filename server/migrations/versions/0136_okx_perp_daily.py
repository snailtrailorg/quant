"""批 102b：OKX 永续日线落 `bar_1d`（**第 2 个加密数据源**——验证「多加密市场共存」）。

**背景**：批 101/101b 已把币安 USDT-M 永续（日线 + 盘中 bar）接入。本批接 OKX 公开行情，
目的不是「多一个源」本身，而是验证两件结构性的事：
1. **同一张 `bar_1d` 容纳两个所的 crypto 行**——靠 `symbol` 后缀（`.BINANCE` / `.OKX`）区分，
   `security_master._market_of_suffix` 两者都归 `crypto`；
2. **crypto bar 链与 provider 无关**——`engine._make_crypto_bar_handler` 一条实现覆盖两所
   （本批把原 `_make_binance_bar_handler` 泛化的直接产出）。

**可达性前提（决定本批的验证方式）**：2026-10-06 实测 **dev/prod 直连 `www.okx.com` 均不可达**
（prod 解析到 `169.254.0.2`＝污染）；只有 **prod 经 AWS 代理出口**（`snailtrail.org:12345`，
102a 的 `proxy_binding.consumer='okx'`）可达。故本批交付＝**代码 + mock 单测**，
真机语义（`1Dutc` 是否被接受、历史深度、限频实际带宽）**留 prod 实测**——见任务文件「待验」。

**本迁移只插两行配置**（expand-only：无 DDL、无 DELETE/RENAME）：
1. `sync_config.okx_perp_daily`——provider=`okx`（0 密钥数据源）、`trade_day_filter='none'`
   （crypto＝连续轴，无交易日历）、`supports_backfill=true`（handler 真读 `backfill_from`）、
   `schedule='40 8 * * *'`（北京 08:40 = 00:40 UTC；**故意与币安日线的 `30 8` 错开 10 分钟**
   ——两个 T+1 任务共享同一 worker 池与（可能的）同一代理出口，错峰省得自相排队；设计 §4.2 的裁定）。
   🔴 **`start_floor` 故意留 NULL**：币安行填 `'2019-09-08'` 是因为那是**已实证**的上线日；
   OKX `history-candles` 的**最早可得日未经验证**（dev 出不到，prod 未实测）——按「start_floor
   只填已实证下界、其余 NULL，不臆造」的既有纪律，本行留空，待 prod 实测后由运维/后续批补。
2. `sync_kind_config.okx_perp_daily`——归置 `(kind=bar_daily, sub_kind=perp)` → `bar_1d`
   （`sub_kind='perp'` 为批 101 已立的子类，本批复用，不新增子类）。

**downgrade**：删这两行（对称）。`bar_1d` 里的 OKX 行**不随配置回滚删除**（数据与配置解耦，
同 0132 口径；如需清数据另走人工）。

**写法**：`op.execute(<内联字面量 SQL>)`（`--sql` 离线渲染安全，同 0132/0133/0134）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0136"
down_revision: Union[str, Sequence[str], None] = "0135"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYNC_ID = "okx_perp_daily"


def upgrade() -> None:
    # 1) sync_config 行（幂等：ON CONFLICT DO NOTHING——尊重运行期改动，复跑不覆盖运维编辑）
    #    start_floor 保持 NULL（未实证，见模块 docstring）
    op.execute(
        "INSERT INTO sync_config (id, name, tushare_api, pg_table, data_type, sync_mode, "
        "schedule, enabled, trade_day_filter, provider, supports_backfill, description) VALUES ("
        "'okx_perp_daily', 'OKX永续日线', 'okx.candles', 'bar_1D', 'crypto', "
        "'incremental', '40 8 * * *', true, 'none', 'okx', true, "
        "'OKX USDT 结算永续日线（www.okx.com history-candles，bar=1Dutc，T+1）。全 USDT-SWAP 标的。"
        "⚠️ prod 必须经代理出口（proxy_binding.consumer=okx）才可达。') "
        "ON CONFLICT (id) DO NOTHING")
    # 2) sync_kind_config 归置行（复用批 101 已立的 bar_daily/perp 子类）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'okx_perp_daily', 'bar_daily', 'perp', 'bar_1d', "
        "'{symbol,ts}', '{open,high,low,close,volume,amount}', '{}', 'incremental') "
        "ON CONFLICT (sync_id) DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM sync_kind_config WHERE sync_id = '{_SYNC_ID}'")
    op.execute(f"DELETE FROM sync_config WHERE id = '{_SYNC_ID}'")
