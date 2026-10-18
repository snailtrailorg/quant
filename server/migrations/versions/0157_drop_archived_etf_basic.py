# EXPAND-CONTRACT: phase=contract pair=0150
"""批 127·Z1：DROP 正名归档表 etf_basic_info_archived_0153（僵尸清理）。

背景：0150 expand 新建 listed_fund_basic 并全量拷贝；0153 contract 把旧表 RENAME
为 *_archived_0153 保留观察。现观察期结束（本地核对：两表 2973 行、ts_code 集合
md5 完全一致），归档表纯冗余，DROP 释放双份存储并消除混淆源。

安全守卫（防误删数据）：DROP 前比对行数——归档行数 > 正名表行数即中止（说明
拷贝不完整/正名表被删过行，须人工核查，不能盲删）。
回滚：downgrade 从 listed_fund_basic 重建归档表（LIKE + 全量拷贝），保证 0153
的 downgrade（RENAME 回 etf_basic_info）在链路回滚时仍可执行。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0157"
down_revision: Union[str, Sequence[str], None] = "0156"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 守卫：归档必须不多于正名表（多=拷贝异常，拒绝 DROP）
    op.execute("""
        DO $$
        DECLARE
            n_arch bigint;
            n_live bigint;
        BEGIN
            SELECT count(*) INTO n_arch FROM etf_basic_info_archived_0153;
            SELECT count(*) INTO n_live FROM listed_fund_basic;
            IF n_arch > n_live THEN
                RAISE EXCEPTION '归档表(%)多于正名表(%)——拷贝异常，拒绝 DROP',
                    n_arch, n_live;
            END IF;
        END $$
    """)
    op.execute("DROP TABLE IF EXISTS etf_basic_info_archived_0153")


def downgrade() -> None:
    # 重建归档表（供 0153 downgrade 的 RENAME 使用）
    op.execute(
        "CREATE TABLE IF NOT EXISTS etf_basic_info_archived_0153 "
        "(LIKE listed_fund_basic INCLUDING ALL)")
    op.execute(
        "INSERT INTO etf_basic_info_archived_0153 SELECT * FROM listed_fund_basic "
        "ON CONFLICT (ts_code) DO NOTHING")
