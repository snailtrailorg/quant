# 盲审 · 修复批次 · 代理 E（2026-08-18，fix-of-fix 检视）

> 评审对象：ff7cd3d（四路盲审修复）+ 7210193（F2 热修）。结论：**1 致命 4 严重——"本 commit 不得部署"，全部确认后已修**（E 判定正确：session_edge 接线断点上线即崩）。

## 致命
- E-1 session_edge 两处调用点未导入（md_hub:433 / hub_worker:292，AST 静态验证）：hub 主循环首轮 ~10s NameError → os._exit(0) → Restart=always ~10s 一循环崩溃重启（gen 每次+1）；worker 定时器 ~5s 即炸。270 测试全绿未抓到（主循环无覆盖）。修：两处 import 补 session_edge + **接线回归测试**（test_session_edge_imported_where_used）。

## 严重（全部确认已修）
- E-2 D-F4 实际没修：注释与代码相反，重启事件照进电平状态机 → 假"恢复"实测复现 + 2h 内后续重启被静默。修：restart_events 直发并从 current/恢复扫描完全排除。
- E-3 R6 零值钳灭：`int(x or -1)` 把合法 0 钳成 -1——hub 时段中重启 sess_ticks=0 恰是 R6 目标场景，永远累加不起。修：哨兵 None 判缺失（evaluate + run_check 载入两处）。
- E-4 D-F6 + 60s 循环告警 = 外部通道配额挤占：断流循环 1 条/min，~1.5h 耗尽 100/天配额，之后真正熔断 critical 被静默跳过。修：外推层 15min 同标题节流（notify:external:* TTL 900）。
- E-5 D-F1 外推腿在目标场景断裂：Valkey 挂时 _quota_exceeded 裸调 _redis 无 try → ConnectionError 上抛 → 外部一条发不出（dep_down(valkey) 恰是最紧急事件）。修：节流键+配额检查全链 fail-open。

## 一般核对（要点）
- C-F1 搬运完整；崩溃窗口语义无变化（max_ts 本就内存态，注释"持久去重"名不副实——已知边界）
- C-F2 闭包安全（_tick_state 先定义）；SELL 无门 R-AV2 成立；hub/direct 分支隔离干净；小疵：拦截计入 broker 失败统计（低优先，记档）
- F2 热修合格：PG 折叠同表、_ensured_tables 双条目=每进程至多一次空操作 DDL 无害；遗留：save_bars_overwrite 无校验（先在）、断流游标照推根因只修一半（回补流程已立，游标语义改进记待办）
- 测试缺口（已补）：session_edge 接线 / direct 门零覆盖（记档）/ C-F1 顺序 / D-F4 假恢复

## 简化机会（已采纳：去掉双重 evaluate；未采纳：timeutil 抽模块/gate 合一——记 16 号实施时一并）
