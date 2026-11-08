# EXPAND-CONTRACT: phase=expand pair=None
"""批 171r：live_task × trading_account 不变量（实盘任务禁用虚拟账户）。

用户裁定（2026-11-08）：「有交易任务必须有登记在册的交易账号，不能绕开系统设计架构」
——不在页面上做兜底并集，改为在**数据层**钉死不变量：
1. FK 已有（fk_live_task_account → trading_account，ON DELETE RESTRICT）保证引用存在；
2. 本迁移补**触发器**：mode='live' 的任务不得引用 is_virtual=true 的账户（纸交易虚拟
   账户只能被 mode='paper' 使用）。任何写入路径（应用/脚本/手工 SQL）一律拒绝。

downgrade：删触发器与函数（不触碰数据）。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0174"
down_revision: Union[str, Sequence[str], None] = "0173"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION live_task_account_guard() RETURNS trigger AS $$
        DECLARE
            v_virtual boolean;
        BEGIN
            SELECT is_virtual INTO v_virtual FROM trading_account WHERE id = NEW.account_id;
            IF v_virtual IS NULL THEN
                RAISE EXCEPTION 'live_task.account_id=% 不存在于 trading_account', NEW.account_id;
            END IF;
            IF NEW.mode = 'live' AND v_virtual THEN
                RAISE EXCEPTION '实盘任务(mode=live)不得绑定虚拟账户(account_id=%, is_virtual=true)',
                    NEW.account_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_live_task_account_guard
        BEFORE INSERT OR UPDATE OF account_id, mode ON live_task
        FOR EACH ROW EXECUTE FUNCTION live_task_account_guard();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_live_task_account_guard ON live_task")
    op.execute("DROP FUNCTION IF EXISTS live_task_account_guard()")
