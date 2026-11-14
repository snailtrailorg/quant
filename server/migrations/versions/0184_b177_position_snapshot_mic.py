"""批177 补漏：position_snapshot 漏出 0183 表清单 → 单独补一次（并加真库守门防再漏）。

背景：0183 覆盖 17 张表，但 position_snapshot（持仓状态表，唯一写入口 _flush_positions）被漏掉，
上产后 /api/position 仍返回 .SHSE/.SZSE。

- 唯一键 (account_id, symbol, direction)：只存在旧写法一种形态（0181/0182 已消除 .SSE），
  转换不会撞键；仍保留尾断断言（撞键则迁移 abort，不静默丢行）。
- downgrade 反向替换（与代码回滚同批）。

Revision ID: 0184
Revises: 0183
"""
from alembic import op

revision = "0184"
down_revision = "0183"


def _chain(reverse: bool = False) -> str:
    if reverse:
        return ("replace(replace(replace(replace(replace(symbol, '.XSHG','.SHSE'), '.XSHE','.SZSE'), "
                "'.BJSE','.BSE'), '.XBIN','.BINANCE'), '.XOKX','.OKX')")
    return ("replace(replace(replace(replace(replace(symbol, '.SHSE','.XSHG'), '.SZSE','.XSHE'), "
            "'.BSE','.BJSE'), '.BINANCE','.XBIN'), '.OKX','.XOKX')")


def upgrade() -> None:
    op.execute("SET LOCAL statement_timeout = 0")
    op.execute("SET LOCAL lock_timeout = 0")
    op.execute(f"UPDATE position_snapshot SET symbol = {_chain()} "
               f"WHERE symbol ~ '\\.(SHSE|SZSE|BSE|BINANCE|OKX)$'")
    n = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM position_snapshot WHERE symbol ~ '\\.(SHSE|SZSE|BSE|BINANCE|OKX)$'").scalar()
    if n:
        raise RuntimeError(f"0184 后 position_snapshot 仍有 {n} 行旧后缀")


def downgrade() -> None:
    op.execute("SET LOCAL statement_timeout = 0")
    op.execute(f"UPDATE position_snapshot SET symbol = {_chain(reverse=True)} "
               f"WHERE symbol ~ '\\.(XSHG|XSHE|BJSE|XBIN|XOKX)$'")
