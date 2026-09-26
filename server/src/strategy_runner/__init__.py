"""
策略实盘化入口（#4 修正版 B）。

每任务独立子进程：systemd quant-live-task@<tid> -> python -m src.strategy_runner.main --task-id <tid>
（批 66a：旧 --id/strategy_config 直启路径已退役，D26 #6）
"""