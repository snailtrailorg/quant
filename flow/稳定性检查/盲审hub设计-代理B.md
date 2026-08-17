# 盲审 · Hub 设计评审 · 代理 B（2026-08-17，Phase 2 对抗评审）

> 评审对象：设计 v1。结论：**修 3 致命 + 8 严重后可实施**（不修 F2 切流当天 bar_1min 毒成双份）。全部已修入设计 v2（§10 修订清单逐条对应）。

## 致命（原文要点）
- **F1** gen 初始化不单调：崩溃重启时 lease(TTL30s) 已过期 → gen 无从推导回落 1 → worker 拒到永远。修：INCR hub:gen
- **F2** 分钟首标注 vs Tushare 分钟末标注错位 1 分钟 + hub 直写 bar_1min → 永不冲突的双份 480 根/日，回测/暖机/diff 全灭。修：末标注+影子期独立表
- **F3** 回放不下单无实现抓手：on_bar 内部触发 place_order，runner 侧无法包装。修：warmup 只填 history 砍 replay 机制（因子无内部状态）

## 严重 S1-S8
seq 矛盾（归零定案）/午休桶丢（11:30:05 补 flush）/volume 未差分（XTP 累计语义）/R-AV3 挂名（gen 跳变重暖机）/全局开关 vs 任务级（md_mode 任务覆盖）/hub 活着行情死（断流自杀+worker 无 bar 冻结）/BLOCK 挂起（socket_timeout+伪代码）/epoch 分钟粒度撞 id

## 陷阱核对（正面确认+修正）
BaseGateway 7 抽象方法/log_level int/协议 str/tick 时区 CHINA_TZ/MAXLEN 5000 现实性确认/XGROUP CREATE-$ 时序/Valkey≥7/noeviction 实例级/冷启动 Valkey 未就绪与 NX 失败区分/td 重连沿需轮询 connect_status/R-TD2 全任务校验

## 简化机会（全采纳）
砍 replay 机制/INCR gen/单流单组+DEL/noeviction 全实例声明/bar_shadow 独立表
