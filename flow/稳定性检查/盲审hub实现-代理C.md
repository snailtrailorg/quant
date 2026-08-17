# 盲审 · Hub 实现代码评审 · 代理 C（2026-08-17，Phase 3 双盲评审）

> 评审对象：src/md_hub/main.py + hub_worker.py + _run_hub_mode + 0040 + unit（commit de085dc 前的代码）。
> 结论：修 C1-C4 再谈影子期。全部已在 de085dc 修复（commit message 逐条对应）。

## 致命（节选，全文见会话归档）
- C1 ctx 键名不匹配（event_engine vs ee）→ hub 模式 100% 启动即崩——证明从未跑起来过
- C2 冻结防线零抓手（_frozen_place_order_gate 全项目 0 调用）+ untrusted 冻结 5s 被 timer 覆盖——F3"无抓手"同型复犯
- C3 订阅无周期幂等重放（F-24 回归）+ 零 tick 时连自杀都不会（first_tick_today 前置）
- C4 跨交易日累计差分不清零 → 每天早盘 volume 全 0

## 严重（S1-S8）
S1 worker 丢 direct 四件套（trade_log/快照/熔断沿/recalc）；S2 stop_check NameError；S3 XAUTOCLAIM 认领即丢弃；S4 回放未来泄漏+ts 格式断裂；S5 影子口径差 1 分钟+缺 amount；S6 收盘桶恒 untrusted；S7 flush 窗口过早+max_ts 未用；S8 epoch 分钟粒度撞 id

## 建议（B1-B6）
B1 seq 失败留洞；B2 退出码；B3 TimeoutError MRO；B4 0 价 tick/seqs 并发；B5 worker unit 依赖/BSE/md_mode 种子；B6 adapters logger 缺失（F-27 路径自身 NameError，direct 也中）

## 正面确认
md_api.connect 7 参/协议 str/log_level int/ThinGateway 赋值时序/TdApi.send_order 兼容/0040 列对齐——均核无误
