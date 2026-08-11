"""
策略实盘化入口（#4 修正版 B）。

每策略独立子进程：systemd quant-strategy@<id> -> python -m src.strategy_runner.main --id <id>
"""