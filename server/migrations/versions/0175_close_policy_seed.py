# EXPAND-CONTRACT: phase=expand pair=None
"""批 171s 件三/件五：平仓品种规则 + 急停重试编排 system_config 种子。

与 `src/data_platform/hub_quote.py` 的 `get_close_policy` / `get_retry_config` 读法逐字对齐：
- `close_policy.xtp.*`：XTP 通道平仓定价规则（每个标量一条，value_type 与 RunConfig.vue
  控件 int/float/bool 逐一对齐，不引入无编辑器的 json 类型）。
- `close_policy.emt.*`：EMT 通道（东财测试柜台真实在跑，账户 3）**独立 provider 配置空间**——
  与 `EmtAdapter.close_policy_provider="emt"` 对应，不误读 xtp 规则；5 条缺省值与 xtp 同。
- 全局重试键：close_all_retry_interval_s / close_all_retry_max / close_all_deadline_s /
  no_quote_empty_max / drain_timeout_s（件五绝对上限 + drain 留痕）。

**crypto 不 seed**：加密实盘 gate 未开（规格 §5 边界），`CryptoPerpAdapter.close_policy_provider=
"crypto"` 读 `close_policy.crypto.*` 恒缺行 → 由读侧 fail-open 回落到代码缺省并 warning 留痕。
gate 开启后补一组同款种子即可，代码零改动。

缺省值与代码常量（`hub_quote._CLOSE_POLICY_DEFAULTS` / `_RETRY_CONFIG`）一致；缺行/DB 失败
由读侧 fail-open 回落到同一组代码缺省并 warning 留痕。

downgrade：删本批全部 key（对称，不动他行；由 _SEEDS 数据驱动，自动覆盖新增 emt 键）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0175"
down_revision: Union[str, Sequence[str], None] = "0174"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (key, value, value_type, description) —— 0112/0076 种子惯例，ON CONFLICT DO NOTHING（幂等）
_SEEDS = [
    ("close_policy.xtp.has_limit_band", "true", "bool",
     "XTP 平仓：本品种是否有涨跌停线（有则跌停价挂单）"),
    ("close_policy.xtp.opponent_depth", "1", "int",
     "XTP 平仓：对手价档位（1=买一/卖一最优档）"),
    ("close_policy.xtp.aggression", "0.10", "float",
     "XTP 平仓：对手价让价幅度（方向感知，SELL 乘 1-a / BUY 乘 1+a）"),
    ("close_policy.xtp.max_order_qty", "100000", "int",
     "XTP 平仓：单笔拆单数量上限"),
    ("close_policy.xtp.price_tick", "0.001", "float",
     "XTP 平仓：挂单价最小价位取整步长"),
    # EMT 独立 provider 配置空间（与 xtp 同缺省值；EmtAdapter.close_policy_provider="emt"）
    ("close_policy.emt.has_limit_band", "true", "bool",
     "EMT 平仓：本品种是否有涨跌停线（有则跌停价挂单）"),
    ("close_policy.emt.opponent_depth", "1", "int",
     "EMT 平仓：对手价档位（1=买一/卖一最优档）"),
    ("close_policy.emt.aggression", "0.10", "float",
     "EMT 平仓：对手价让价幅度（方向感知，SELL 乘 1-a / BUY 乘 1+a）"),
    ("close_policy.emt.max_order_qty", "100000", "int",
     "EMT 平仓：单笔拆单数量上限"),
    ("close_policy.emt.price_tick", "0.001", "float",
     "EMT 平仓：挂单价最小价位取整步长"),
    ("close_all_retry_interval_s", "5", "int",
     "急停平仓：每轮重试间隔（秒）"),
    ("close_all_retry_max", "10", "int",
     "急停平仓：最大重试轮数（绝对次数上限）"),
    ("close_all_deadline_s", "120", "int",
     "急停平仓：绝对超时上限（秒，覆盖订阅生效窗 1-2min）"),
    ("no_quote_empty_max", "6", "int",
     "急停平仓：连续无报价空轮上限"),
    ("drain_timeout_s", "5", "int",
     "急停平仓：退出前专用线程 drain/join 限时（秒）"),
]


def upgrade() -> None:
    for key, value, vtype, desc in _SEEDS:
        op.execute(
            "INSERT INTO system_config (key, value, value_type, description) "
            f"VALUES ('{key}', '{value}', '{vtype}', '{desc}') "
            "ON CONFLICT (key) DO NOTHING")


def downgrade() -> None:
    for key, _, _, _ in _SEEDS:
        op.execute(f"DELETE FROM system_config WHERE key = '{key}'")
