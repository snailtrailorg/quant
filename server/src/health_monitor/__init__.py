"""健康监控模块（docs/architecture/15-服务监控设计.md）。

分层（S6 修订 2026-08-18 的产物——重启是 liveness 工具不是疗法）：
- collector：采集快照（systemd unit 状态 + Valkey 心跳 + 依赖可达），/metrics 与 beat 任务共用
- monitor：症状型规则判定 + 沿检测（触发/恢复）+ health_event 落库 + MessageChannel 告警
- 日历/时段规则只做告警抑制，永不触发动作
"""
