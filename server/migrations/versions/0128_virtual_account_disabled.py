"""虚拟账户 enabled=false——hbcheck 期望集契约修正（批 88 彩排红的根因修复）。

## 彩排实录（2026-10-03 staging `202610030826-072149c` 阶段 8 红，rescue 自动回滚）

0127 种的虚拟账户行 `enabled=true`，而 quant-hbcheck v2 的期望集＝「交易域 **enabled** 行」
逐账户验心跳（deploy/wrappers/quant-hbcheck:64 `SELECT id FROM trading_account WHERE enabled`，
「期望集层面不过滤防漏报」是 66b 的刻意立法）。⇒ 虚拟账户入表即生成期望行（staging 实测
id=502），但 md-hub 不为它写心跳（`quant:hb:md-hub:502` 永缺——虚拟账户没有真实行情流/订阅，
写心跳＝谎报健康）。结果：**任何一次含 0127 的 release 在阶段 8「hub 心跳复现」必红 ⇒
rescue 自动回滚 ⇒ 永久上不了产**。staging 彩排实录：✗ 行 id=502 键缺失（期望 4 行 1 行不健康）。

## 为什么修在数据侧而不是 wrapper

1. **语义**：`enabled` 在 trading_account 的语义＝「启用中的真实交易通道」。虚拟账户不是
   通道——没有 provider 凭据、没有行情订阅、不该出现在任何「通道健康」期望集里。
   enabled=false 是把它的真实身份写对，**不是为了让闸门变绿而关闸门**。
2. **位置**：wrapper 是装位件（/usr/local/sbin，不随 release 版本化，改动须 michael 交办单
   staging+prod 各一次）；且「期望集排除 is_virtual」是把数据契约藏进部署脚本。契约应该
   长在数据本身（enabled=false）＋本注释里。wrapper 头注「期望集=enabled 行」保持原样。
3. **零连带（落码前逐点验过）**：纸任务链路只认 `is_virtual`——`paper_trade.py:47
   get_virtual_account_id()` 的 SQL 无 enabled 条件；`create_live_task` paper 分支强制覆盖
   account_id（trading.py:125）；建任务下拉 `/api/account` 读的是 **accounts 旧表**（0042/0027，
   与 trading_account 非同一张——0127 头注辨析过），虚拟账户天然不在任何下拉。

## pytest/往返为什么当初没逮住

期望集是 wrapper 的**部署链运行时逻辑**：pytest 全 mock、往返只验 schema 形状，都够不着。
这是「动交易域表必审 deploy/wrappers/ 读点」立法（批 83a 立，`test_deploy_blast_radius.py`
闸门）的**再犯实证**——0127 落码时没有对 `trading_account` 的写入查 wrapper 读点。
防线：本注释即哨兵；往返新增**用例 L**（0128 专项：enabled=false/复跑幂等/降级恢复/不误伤）。

Revision ID: 0128
Revises: 0127
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0128"
down_revision: Union[str, None] = "0127"
branch_labels: Union[Sequence[Union[str, None]], str] = None
depends_on: Union[Sequence[Union[str, None]], str] = None


def upgrade() -> None:
    # WHERE 双条件幂等：已 false 的行不再碰（复跑零写放大）。
    op.execute("UPDATE trading_account SET enabled = false "
               "WHERE is_virtual = true AND enabled")


def downgrade() -> None:
    # 对称恢复 0127 形态（虚拟账户行在 0127 语义里 enabled=true）。
    op.execute("UPDATE trading_account SET enabled = true "
               "WHERE is_virtual = true AND NOT enabled")
