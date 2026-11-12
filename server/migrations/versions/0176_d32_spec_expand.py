# EXPAND-CONTRACT: phase=expand pair=0177
"""批 172c P2a：规格层扩列 + tick 归一化 + close_policy 迁 params（expand 步）。

**本迁移不删 system_config 旧键、不切 adapters 读侧**——contract 步在 0177（P2b，P2a 上产后）。

变更：
1. security_master 加规格列（sellable_rule/order_types/position_side/venue_flags/
   order_range_override/effective_from/source/version）。
2. band_rules 加申报范围列（order_range_mode/lower_pct/upper_pct + CHECK）。
3. tick 归一化：stock=0.01；listed_fund/convertible/fund/reits=0.001；perp 保留行值但断言 >0。
4. trading_account.params.close_policy 回填（provider 维：opponent_depth/aggression/
   max_order_qty；已有其他 params 键不覆盖）。
5. 4 段 category 级 close_policy 键存在则 raise（当前无；禁静默丢配置）。

downgrade：
- 删列/约束；删除 params->'close_policy'（仅本嵌套键，其他 params 保留）。
- system_config 旧键未动（expand 步），无需恢复。
- tick 归一化不恢复（数据修正，旧 SZSE 0.001 坏值应由上游重灌；见批172c方案 v3）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0176"
down_revision: Union[str, Sequence[str], None] = "0175"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK_POSITION = "ck_security_master_position_side"
_CHECK_RANGE = "ck_band_rules_order_range_mode"


def upgrade() -> None:
    # 1. security_master 规格列
    op.add_column("security_master", sa.Column(
        "sellable_rule", sa.Text(), nullable=False, server_default="volume_minus_frozen"))
    op.add_column("security_master", sa.Column(
        "order_types", sa.dialects.postgresql.JSONB(), nullable=False,
        server_default=sa.text("'[]'::jsonb")))
    op.add_column("security_master", sa.Column(
        "position_side", sa.Text(), nullable=False, server_default="long_only"))
    op.add_column("security_master", sa.Column(
        "venue_flags", sa.dialects.postgresql.JSONB(), nullable=False,
        server_default=sa.text("'{}'::jsonb")))
    op.add_column("security_master", sa.Column(
        "order_range_override", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("security_master", sa.Column("effective_from", sa.Date(), nullable=True))
    op.add_column("security_master", sa.Column("source", sa.Text(), nullable=True))
    op.add_column("security_master", sa.Column(
        "version", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint(
        _CHECK_POSITION, "security_master",
        "position_side IN ('long_only','short_allowed','both')")

    # 2. band_rules 申报范围列
    op.add_column("band_rules", sa.Column(
        "has_limit_band", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("band_rules", sa.Column(
        "order_range_mode", sa.Text(), nullable=False, server_default="none"))
    op.add_column("band_rules", sa.Column("order_range_lower_pct", sa.Numeric(), nullable=True))
    op.add_column("band_rules", sa.Column("order_range_upper_pct", sa.Numeric(), nullable=True))
    op.create_check_constraint(
        _CHECK_RANGE, "band_rules",
        "order_range_mode IN ('pct_of_ref','abs','venue','none')")

    # 3. account_snapshot 保证金/权益列（perp 现金门；无写入方时保持 NULL=未知）
    op.add_column("account_snapshot", sa.Column("margin_used", sa.Numeric(), nullable=True))
    op.add_column("account_snapshot", sa.Column("equity", sa.Numeric(), nullable=True))

    # 4. tick 归一化（category 事实；perp 无行情源，只断言 >0）
    op.execute("UPDATE security_master SET tick_size=0.01 "
               "WHERE category='stock' AND (tick_size IS NULL OR tick_size<>0.01)")
    op.execute("UPDATE security_master SET tick_size=0.001 "
               "WHERE category IN ('listed_fund','convertible','fund','reits') "
               "AND (tick_size IS NULL OR tick_size<>0.001)")
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM security_master
                     WHERE category='perp' AND (tick_size IS NULL OR tick_size<=0)) THEN
            RAISE EXCEPTION 'security_master: perp tick_size 存在 NULL/<=0，需上游修复后再迁移';
          END IF;
        END $$;
    """)
    # perp 的 tick 全量 0.01 是历史填充，不是交易所事实：不洗成 migration 事实，打未校验标记。
    op.execute("UPDATE security_master SET source='migration_0176', version=1 "
               "WHERE source IS NULL AND category<>'perp'")
    op.execute("UPDATE security_master SET source='legacy_unverified', version=1, "
               "venue_flags = coalesce(venue_flags,'{}'::jsonb) || '{\"tick_unverified\": true}'::jsonb "
               "WHERE category='perp' AND source IS NULL")

    # 4. 防丢：4 段 category 级 close_policy 键（3 个点）当前不应存在
    op.execute("""
        DO $$
        DECLARE n int;
        BEGIN
          SELECT count(*) INTO n FROM system_config
          WHERE key LIKE 'close_policy.%.%.%';
          IF n > 0 THEN
            RAISE EXCEPTION '存在 category 级 close_policy 键 % 条，P2a 不支持静默迁移', n;
          END IF;
        END $$;
    """)

    # 5. params.close_policy 回填（provider 维；无对应键的 provider 不写入）
    op.execute("""
        UPDATE trading_account ta
        SET params = jsonb_set(
            coalesce(ta.params, '{}'::jsonb),
            '{close_policy}',
            jsonb_build_object(
              'opponent_depth', coalesce((
                  SELECT value::int FROM system_config
                  WHERE key = 'close_policy.' || ta.provider || '.opponent_depth'), 1),
              'aggression', coalesce((
                  SELECT value::float FROM system_config
                  WHERE key = 'close_policy.' || ta.provider || '.aggression'), 0.10),
              'max_order_qty', coalesce((
                  SELECT value::int FROM system_config
                  WHERE key = 'close_policy.' || ta.provider || '.max_order_qty'), 100000)
            ),
            true)
        WHERE EXISTS (
            SELECT 1 FROM system_config
            WHERE key LIKE 'close_policy.' || ta.provider || '.%');
    """)


def downgrade() -> None:
    # 同 provider 多账号 close_policy 已分叉 → 拒绝静默删；交由人处理
    op.execute("""
        DO $$
        DECLARE n int;
        BEGIN
          SELECT count(*) INTO n FROM (
            SELECT provider, count(DISTINCT (params->'close_policy')::text) AS k
            FROM trading_account WHERE params ? 'close_policy' GROUP BY provider
          ) x WHERE x.k > 1;
          IF n > 0 THEN
            RAISE EXCEPTION 'trading_account.params.close_policy 在 provider 内已分叉，拒绝 downgrade 静默删除';
          END IF;
        END $$;
    """)
    op.execute("UPDATE trading_account SET params = params - 'close_policy' "
               "WHERE params ? 'close_policy'")
    op.drop_column("account_snapshot", "equity")
    op.drop_column("account_snapshot", "margin_used")
    op.drop_constraint(_CHECK_RANGE, "band_rules", type_="check")
    op.drop_column("band_rules", "order_range_upper_pct")
    op.drop_column("band_rules", "order_range_lower_pct")
    op.drop_column("band_rules", "order_range_mode")
    op.drop_column("band_rules", "has_limit_band")
    op.drop_constraint(_CHECK_POSITION, "security_master", type_="check")
    for col in ("version", "source", "effective_from", "order_range_override",
                "venue_flags", "position_side", "order_types", "sellable_rule"):
        op.drop_column("security_master", col)
