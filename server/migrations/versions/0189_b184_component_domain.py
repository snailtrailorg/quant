"""批184a：组件台账域归一——source 收回「生态」，OS 实现另立 pkg_mgr。

背景（`flow/方案/批184-组件台账归一-方案.md`）：`source` 一词两义——
grype/SBOM 台账用它表示**生态**（pip/rpm/binary），dep_audit 用它表示**判定来源**
（system=dnf 纳管）。schema2 把 OS 行从「8 个点名组件」扩成全量 rpm ⇒ 每个 rpm 包
同时存在 `system`（含判定）与 `rpm`（SBOM 名/版本）两行，页面同名冲突
（tzdata「有新版但 dnf 无更新」即此）。

本迁移只动数据形态，不改唯一键 (source,name)：
1. `system` 行的判定并入同名 `rpm` 行，随后删除 `system` 行与 8 个别名行；
2. `rpm`/`system` 统一为 `os`（**域**，apt 环境下同一域，实现见 pkg_mgr）；
3. 新增 `pkg_mgr`（dnf/apt，NULL=未判定，由首轮 scan 按契约写入——迁移不猜）；
4. `managed_by` 按域回填后只读（pypi/repo/none）；
5. CHECK 锁死域与实现值域，防第四个源再长出来。

不删历史行（除必须合并/退役的 system 与别名行）；孤儿 GC 由 scan 统一按 last_seen 执行。

Revision ID: 0189
Revises: 0188
"""
import sqlalchemy as sa
from alembic import op

revision = "0189"
down_revision = "0188"

# schema1 的点名探针别名（不是包名）：真实包名 python3.11/postgresql-server 等由全量 OS 行覆盖
_ALIAS = ("python", "nginx", "openssl", "postgres", "redis",
          "pango", "cairo", "wqy-microhei")

_ALIAS_SQL = ", ".join("'" + a + "'" for a in _ALIAS)   # 常量字面量，无注入面


def upgrade() -> None:
    op.add_column("security_component",
                  sa.Column("pkg_mgr", sa.Text(), nullable=True))

    # ① system 行（dnf 权威）的版本/判定并入同名 rpm 行
    op.execute("""
        UPDATE security_component AS r SET
            version        = s.version,
            latest_version = COALESCE(s.latest_version, r.latest_version),
            latest_source  = COALESCE(s.latest_source,  r.latest_source),
            judge_class    = COALESCE(s.judge_class,    r.judge_class),
            judge_why      = COALESCE(s.judge_why,      r.judge_why),
            checked_at     = GREATEST(r.checked_at, s.checked_at),
            first_seen     = LEAST(r.first_seen, s.first_seen),
            last_seen      = GREATEST(r.last_seen, s.last_seen)
        FROM security_component AS s
        WHERE r.source = 'rpm' AND s.source = 'system' AND r.name = s.name
    """)

    # ② 删已被合并的 system 行 + 8 个别名行（别名不是包名，留着只会造成同名歧义）
    op.execute(f"""
        DELETE FROM security_component s
        WHERE s.source = 'system'
          AND (EXISTS (SELECT 1 FROM security_component r
                       WHERE r.source = 'rpm' AND r.name = s.name)
               OR s.name IN ({_ALIAS_SQL}))
    """)

    # ③ 域归一：rpm/system → os
    op.execute("UPDATE security_component SET source = 'os' "
               "WHERE source IN ('rpm', 'system')")

    # ④ managed_by 由域派生（此后只读，写侧不再覆盖）
    op.execute("""
        UPDATE security_component SET managed_by = CASE source
            WHEN 'pip' THEN 'pypi'
            WHEN 'os'  THEN 'repo'
            ELSE 'none' END
        WHERE source IN ('pip', 'os', 'binary')
    """)

    # ⑤ 值域锁死
    op.execute("ALTER TABLE security_component ADD CONSTRAINT ck_sc_domain "
               "CHECK (source IN ('pip', 'os', 'binary'))")
    op.execute("ALTER TABLE security_component ADD CONSTRAINT ck_sc_pkg_mgr "
               "CHECK (pkg_mgr IS NULL OR pkg_mgr IN ('dnf', 'apt'))")


def downgrade() -> None:
    # 注意：system/rpm 的拆分不可恢复（已合并）——回滚到「rpm 域」语义，
    # 旧代码按 source='system' 读 OS 行会读不到 ⇒ 回滚须整版本回退（项目既有约定）。
    op.execute("ALTER TABLE security_component DROP CONSTRAINT IF EXISTS ck_sc_pkg_mgr")
    op.execute("ALTER TABLE security_component DROP CONSTRAINT IF EXISTS ck_sc_domain")
    op.execute("UPDATE security_component SET source = 'rpm' WHERE source = 'os'")
    # managed_by 保持现值（回滚后旧代码仍用它渲染「安装途径」；清零会造成可见退化）
    op.drop_column("security_component", "pkg_mgr")
