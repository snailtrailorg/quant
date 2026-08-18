# 盲审 · 监控 Phase 1 代码 · 代理 D（2026-08-18，实施级）

> 评审对象：commit 70ec2f9 中 health_monitor/端点/迁移/beat 部分。
> 结论：**2 致命 4 严重，全数修复随同日部署**。最狠的两条：Valkey 挂=告警链先死（与被监控对象共死）；/metrics 输出非法 Prometheus 格式。

## 致命
- **F1 Valkey 宕机=零告警**：run_check 把沿检测/告警全包一个 try，r.get 抛 ConnectionError 即跳过——evaluate 算出的 dep_down(valkey) critical 永远到不了 _notify。恰在最需要告警的时刻断链，违反平台自己的 SE1 决策（notify 故障降级继续发）。修：通知循环移出 try；Valkey 不可用时降级无去重直发（+回归测试锁死）。
- **F2 每次 hub 重启（含每日 deploy）≈必产假告警对**：RestartSec=30 → 单元 ≥31s 处 activating(auto-restart)，30s 轮询必中 unit_down critical；首跳心跳 60s+ 逼近 hb TTL 90s → hub_hb_lost 再叠一条，随后各跟假"恢复"。修：R1 跳过 SubState=auto-restart（R2 计数沿会报）；R4 需连续 2 轮缺失。

## 严重
- **F3 /metrics 非法格式（本机复现）**：emit 去重 `l.split(" ")[0]` 取到 "#"——每个 series 重复一组 HELP/TYPE 穿插样本间，严格解析器整个 target 拒收。修：按指标族分组输出（HELP/TYPE 每族一组在前），+多 unit 唯一性测试。
- **F4 计数型重启事件进电平沿状态机**：每起重启 30s 后必跟假"恢复"通知+假 recovery 行。修：unit_restarted 绕过状态机直发（计数器单调自带沿）。
- **F5 采集盲区翻"恢复"**：systemctl 超时返回 {} → unit_down 恢复扫描删 state 键发假 all-clear，下轮又 critical 双份噪音。修：证据缺失≠证据健康——units 空/采集失败跳过 R1 判定与 unit_down 恢复扫描。
- **F6 PG 宕机告警无处可达**：notify 的 system 类永不外推（只落 PG notifications，PG 挂时 insert 也失败）。与 F1 合成"两个依赖宕机都到不了人"。修：should_push_external 扩为 critical + (risk|system)——基础设施紧急到人；warn 级仍站内。

## 陷阱核对（要点/处置）
- **nginx 未路由 /metrics /readyz**（SPA catch-all 返回 200 index.html）——Phase 2 Zabbix 落地时必须同步加路由+限源，否则拉域名静默拿空指标（记入 SM1）
- beat 30s 打 risk 队列（worker -c 1）：可共存；加 `expires=25` 防停机堆积连环补跑 ✓
- *_total 计数器声明成 gauge（rate() 不可用）→ 改 counter 类型 ✓
- health_event 无界 → 30 天保留期清理（每日一轮）✓
- readyz 手搓第二套探测 → 复用 collect() 统一口径 ✓
- 死代码（SEV_ORDER/discover_feishu_units/hb events 字段）→ 删 ✓
- hm 心跳可能被长 risk 任务延迟 ≤5min → Phase 2 Zabbix 触发阈值要考虑（记入 SM1）

## 简化机会（采纳）
readyz 复用 collect；死代码清理。未做：health_event 分区（速率低，保留期清理足够）。

（裁定：F1-F6/S 全确认全修；nginx 路由与 Zabbix 触发阈值归 SM1 Phase 2）
