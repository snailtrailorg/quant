"""批 118：factor_def 加 `needs` JSONB 列（因子多维数据依赖声明持久化）。

母设计：`docs/design/D27-暖机与切换.md`；方案＝`flow/任务/批118-因子数据面多维声明与暖机自适应.md`
（步 2 双盲审返工收窄版——引擎侧：needs 真源/持久化/暖机自适应/判定语义）。

**为什么加这列**：`needs_history` 是单一 int 列——因子对多 kind（bar_minute/
bar_daily/…）的依赖声明无持久化通道，Web 声明多维无处存、runner 重启 load 丢
（静默降级单频，批 117 同族失效模式）。本列＝needs dict 的唯一持久化通道。

**语义（expand-only 加列，无声明头——同 0139/0140 纯加列惯例）**：
- `NULL` ≡ 旧 int 糖映射：load 侧自动升格 `{"bar_minute": needs_history}`（存量
  因子行为零漂移）；
- 非 NULL＝JSONB object，键必须 ∈ `DATA_KINDS`（`quant_common.contract` 唯一词表，
  写侧 `validate_needs` 拦截——DB 侧不加键域 CHECK：词表真源在代码，DDL 复制词表=第二真源；
  但按 jsonb 类型守卫闸门（tests/test_jsonb_columns_guarded.py）加**形状律** object CHECK
  ——拦双重编码静默降级（json.dumps 双包产 string），与键域立法正交）。

`ADD COLUMN IF NOT EXISTS`（部署中断强制复跑安全，仓例 0059）。
全部 `op.execute(<内联字面量>)`（`--sql` 离线渲染安全，同 0132~0143）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0144"
down_revision: Union[str, Sequence[str], None] = "0143"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE factor_def ADD COLUMN IF NOT EXISTS needs jsonb NULL"
    )
    # jsonb 类型守卫（三条律之 object，仓例 0120）：非 NULL 必须是 object——
    # 拦 json.dumps 双重编码产 string 的静默降级（23514 在存储层咬住）。
    op.execute(
        "ALTER TABLE factor_def DROP CONSTRAINT IF EXISTS factor_def_needs_shape_chk"
    )
    op.execute(
        "ALTER TABLE factor_def ADD CONSTRAINT factor_def_needs_shape_chk "
        "CHECK (needs IS NULL OR jsonb_typeof(needs) = 'object')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE factor_def DROP COLUMN IF EXISTS needs")
