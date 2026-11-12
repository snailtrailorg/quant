# EXPAND-CONTRACT: phase=contract pair=0176
"""批 172c P2b：退役 system_config close_policy.*（contract 步）。

前置：0176（expand）已把 provider 级策略回填到 trading_account.params.close_policy。
本步先对账 P2a 期间的运维改动（settings 页仍可写这 10 键）——与 params 不同则以
system_config 为准覆盖 params；同 provider 多账号已分叉则 raise（禁静默丢配置）。
然后删 10 个键。

downgrade：从 params.close_policy 按 provider 回写 system_config（值冲突 raise），
键值按 0175 种子结构恢复；不承诺保留 P2a 后的规格编辑（生产回滚走整版本 rollback）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0177"
down_revision: Union[str, Sequence[str], None] = "0176"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ATTRS = ("opponent_depth", "aggression", "max_order_qty", "price_tick", "has_limit_band")
_VALUE_TYPES = {"opponent_depth": "int", "aggression": "float", "max_order_qty": "int",
                "price_tick": "float", "has_limit_band": "bool"}


def upgrade() -> None:
    # 1. 对账：P2a 窗口内 settings 页改动 vs params 回填值
    op.execute("""
        DO $$
        DECLARE r record; v text; p jsonb;
        BEGIN
          FOR r IN
            SELECT DISTINCT ta.id, ta.provider,
                   'close_policy.' || ta.provider || '.' || a.attr AS key
            FROM trading_account ta
            CROSS JOIN (VALUES ('opponent_depth'),('aggression'),('max_order_qty')) AS a(attr)
            JOIN system_config sc ON sc.key = 'close_policy.' || ta.provider || '.' || a.attr
            WHERE ta.params ? 'close_policy'
          LOOP
            SELECT sc.value INTO v FROM system_config sc WHERE sc.key = r.key;
            SELECT ta.params->'close_policy' INTO p FROM trading_account ta WHERE ta.id = r.id;
            IF v IS NOT NULL AND p IS NOT NULL
               AND (p->>split_part(r.key, '.', 3)) IS DISTINCT FROM v THEN
              UPDATE trading_account
                 SET params = jsonb_set(params, ARRAY['close_policy', split_part(r.key, '.', 3)],
                                        to_jsonb(v::numeric), true)
               WHERE id = r.id;
            END IF;
          END LOOP;
        END $$;
    """)

    # 2. 同 provider 多账号分叉检测后删键
    op.execute("""
        DO $$
        DECLARE n int;
        BEGIN
          SELECT count(*) INTO n FROM (
            SELECT provider, count(DISTINCT (params->'close_policy')::text) AS k
            FROM trading_account WHERE params ? 'close_policy' GROUP BY provider
          ) x WHERE x.k > 1;
          IF n > 0 THEN
            RAISE EXCEPTION 'trading_account.params.close_policy 在 provider 内已分叉，拒绝删除 system_config 旧键';
          END IF;
        END $$;
    """)
    # 3. 类型归一：三个数值键必须是 JSON number（早期回填可能落成字符串）
    op.execute("""
        UPDATE trading_account
           SET params = jsonb_set(params, '{close_policy}', jsonb_build_object(
                 'opponent_depth', (params->'close_policy'->>'opponent_depth')::int,
                 'aggression', (params->'close_policy'->>'aggression')::float8,
                 'max_order_qty', (params->'close_policy'->>'max_order_qty')::int), true)
         WHERE params ? 'close_policy'
    """)
    op.execute("DELETE FROM system_config WHERE key LIKE 'close_policy.%'")


def downgrade() -> None:
    import sqlalchemy as sa

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT provider, count(DISTINCT concat_ws('|', "
        "         (params->'close_policy'->>'opponent_depth')::int, "
        "         round((params->'close_policy'->>'aggression')::numeric, 6), "
        "         (params->'close_policy'->>'max_order_qty')::int)) AS k, "
        "       min((params->'close_policy')::text) AS pol "
        "FROM trading_account WHERE params ? 'close_policy' GROUP BY provider")).fetchall()
    import json as _json
    for provider, k, pol in rows:
        if k and k > 1:
            raise RuntimeError(f"provider={provider} 多账号 close_policy 分叉，downgrade 无法回写")
        if not pol:
            continue
        data = _json.loads(pol)
        for attr, typ in _VALUE_TYPES.items():
            if attr not in data:
                continue
            conn.execute(sa.text(
                "INSERT INTO system_config (key, value, value_type, description) "
                "VALUES (:k, :v, :t, :d) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"),
                {"k": f"close_policy.{provider}.{attr}", "v": str(data[attr]), "t": typ,
                 "d": f"close_policy {attr}（0177 downgrade 回写）"})
