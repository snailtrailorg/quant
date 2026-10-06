"""批 107：供给面 7 字面量项收编（补归置行 + 修 `sync_config.pg_table` 错配）。

**本迁移只插/改三行配置**（expand-only：无 DDL、无 DELETE/RENAME）：
1. `sync_kind_config.static_symbols`——归置 `(static_list, symbols)` → `static_symbols`
   （该 sync_id 自 83b 收编起**只有 `sync_config` 行、无归置行** ⇒ ① 无 `(kind, sub_kind)` 可依
   ② 批 107 的 `fetch_supply` 分派无键）。列声明：`ts_code` PK，供给侧文本列 `name/industry`
   （`list_status='L'`/`delisted=false` 为 handler 写入的常量、`updated_at` 走 DB 默认）。
2. `sync_kind_config.convertible_terms`——归置 `(static_list, terms)` → `convertible_terms`。
   `terms` 是 **jsonb**（不在 `float_cols`/`text_cols` 分类内），由 bespoke 写入持有。
3. **数据修复（同族真源错配）**：`sync_config.etf_list.pg_table` 原为 `asset_static_info`
   （**错**——handler 实际写 `etf_basic_info`，`sync_kind_config` 亦为 `etf_basic_info`）。
   危害**不止台账错**：`DELETE /api/sync/data/{sid}`（`web_api/routes/sync.py`）
   直接 `DELETE FROM "{pg_table}"` ⇒ 清空 `etf_list` 会**删掉 A 股整表 `asset_static_info`**
   （`astock_list` 的落表）＝**跨项数据丢失**。全库比对仅此一处错配（其余 `lower()` 后一致）。

**为什么补归置行（而非保留「无归置行」特例）**：批 107 把 7 字面量项的拉取改经
`adapter.fetch_supply(kind, sub_kind)` —— `(kind, sub_kind)` 是分派键，缺归置行即无键
（`static_symbols`/`convertible_terms` 会拉不动）。子类名 `symbols`/`terms` 与 `cb_basic` 的
`convertible` 区分开，保持「一项一键」不歧义。

**downgrade**：删两行归置 + 把 `etf_list.pg_table` 复原为 `asset_static_info`（对称；
但注意复原即复原那个 DELETE 错表 bug——仅用于迁移回滚的一致性）。

**写法**：`op.execute(<内联字面量 SQL>)`——离线渲染（`alembic upgrade --sql`）下
`exec_driver_sql` 会炸（同 0132/0133 的教训）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0138"
down_revision: Union[str, Sequence[str], None] = "0137"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) static_symbols 归置行（幂等：ON CONFLICT DO NOTHING——复跑不覆盖运维编辑）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'static_symbols', 'static_list', 'symbols', 'static_symbols', "
        "'{ts_code}', '{}', '{name,industry}', 'full_rebuild') "
        "ON CONFLICT (sync_id) DO NOTHING")
    # 2) convertible_terms 归置行（terms 为 jsonb，不进 float/text 分类）
    op.execute(
        "INSERT INTO sync_kind_config (sync_id, kind, sub_kind, pg_table, pk_cols, "
        "float_cols, text_cols, rebuild) VALUES ("
        "'convertible_terms', 'static_list', 'terms', 'convertible_terms', "
        "'{ts_code}', '{}', '{}', 'full_rebuild') "
        "ON CONFLICT (sync_id) DO NOTHING")
    # 3) 数据修复：etf_list 的 pg_table 错配（清空会删错表 = 跨项数据丢失）
    op.execute(
        "UPDATE sync_config SET pg_table='etf_basic_info' "
        "WHERE id='etf_list' AND pg_table='asset_static_info'")


def downgrade() -> None:
    op.execute("DELETE FROM sync_kind_config WHERE sync_id='static_symbols'")
    op.execute("DELETE FROM sync_kind_config WHERE sync_id='convertible_terms'")
    op.execute(
        "UPDATE sync_config SET pg_table='asset_static_info' "
        "WHERE id='etf_list' AND pg_table='etf_basic_info'")
