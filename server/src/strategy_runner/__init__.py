"""
策略实盘化入口（#4 修正版 B）。

每任务独立子进程：systemd quant-live-task@<tid> -> python -m src.strategy_runner.main --task-id <tid>
（批 66a：旧 --id/strategy_config 直启路径已退役，D26 #6）
"""

# ——— 暖机常量单一真源（批 118，D27）———
# 消费点 4 处（原 4 处硬编码全收敛至此）：main._warmup_history（基线窗+读库窗）、
# main 判定三分支（cap 拒启）、hub_worker._warmup_from_stream（流回放窗）、
# hub_worker rewarm/history 裁剪（运行窗）。
WARMUP_BASELINE = 100    # 暖机基线窗（根）：无 bar_minute 声明时的默认历史窗
WARMUP_CAP = 5000        # 暖机声明上限（根）：bar_minute 声明 >cap ⇒ EX_CONFIG 拒启且报上限（不静默截）
