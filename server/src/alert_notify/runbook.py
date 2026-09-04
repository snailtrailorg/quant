"""runbook 单源映射（W3 通知收官批 2026-09-01）。

code → {label, guide}：通知结构化标识 → 展示名+一句话处置。
- 消费方①：站内通知（前端 GET /api/runbook → MainLayout 缓存 → chip/guide 渲染）
- 消费方②：外推通道（notify._push_channel 组装"▸ 处置"行）
- **暂仅中文**（多语言债：governed by multilang-architecture，en 版本批后补）
一致性防线：直调打码点 code 字面量 ⊆ RUNBOOK 键（tests/test_notify.py 一致性测试跑 collect 断言；
wrapper 变量透传链测不到——main._alert / make_alert 注入链靠人工对照，见测试盲区声明）。
"""

RUNBOOK: dict[str, dict] = {
    # ——— L3/SA4 自愈族（scheduler） ———
    "l3.failed":        {"label": "L3 拉起失败", "guide": "systemctl start 非零退出——查 journalctl -u <unit> 与 StartLimit 窗口；L3 按 300s 周期重试。"},
    "l3.pull":          {"label": "L3 自动拉起", "guide": "期望源在而 systemd 无实例——若非预期，先在 Web 停任务或打 hub 维护标记。"},
    "l3.skip-valkey":   {"label": "L3 fail-closed 跳过", "guide": "Valkey 不可达无法验租约，本轮不拉起（防盲拉第二实例破坏 fencing）——查 Valkey。"},
    "sa4.restart":      {"label": "SA4 自动重启", "guide": "崩溃退避拉起中；连续出现查该单元 journalctl 归因首个异常。"},
    "hub.maint":        {"label": "hub 维护窗跳过", "guide": "维护标记在场 hub 不自动拉起——维护完成删标记（TTL 4h 自动过期）。"},
    "unit.failed":      {"label": "单元 Failed", "guide": "OnFailure 钩子告警——journalctl -u <unit> 归因；EX_CONFIG(78) 类不重启需人工。"},
    "unit.config-err":  {"label": "单元配置错", "guide": "EX_CONFIG 78 配置错误不自动重启——核对任务配置/凭证后手动 start。"},
    # ——— 批 7 告警分发（2026-09-02） ———
    "alert.push-failed": {"label": "告警推送失败", "guide": "外推通道发送失败——设置→告警 查通道状态并测试；连续失败查 risk worker journalctl 归因。"},
    "alert.test":        {"label": "告警测试", "guide": "设置→告警 的通道测试消息（人工触发），无行动项。"},
    "im.first-seen":     {"label": "飞书新用户首见", "guide": "首次给 bot 发消息者已按 default_role 自动登记（同时成告警收件人）——非预期则到 设置→集成→IM→用户管理 调整。"},
    # ——— 数据面（hub/worker） ———
    "deps.exhausted":   {"label": "依赖探活耗尽", "guide": "PG 持续不可达超 10 分钟——查 PG 服务与网络；runner 已退出待 systemd 重试。"},
    "frozen.stream":    {"label": "流异常冻结", "guide": "流序号跳变/不可信 bar（数据污染事实）——重启任务解冻；SELL 不受限。"},
    "frozen.intercept": {"label": "冻结期拦截 BUY", "guide": "不可信 bar/流 gap（数据污染事实）——重启任务解冻；SELL 不受限。"},
    "buy.blocked":      {"label": "BUY 拦截(数据不新鲜)", "guide": "bar 停更>300s 或 hub 心跳丢失——查 hub 状态与行情流；恢复后自动放行。"},
    "buy.blind":        {"label": "任务盲视", "guide": "hub 心跳丢失或 bar 停更——数据面不驱动决策中；查 hub 与流消费。"},
    "hub.xadd-fail":    {"label": "hub XADD 失败", "guide": "bar 写流失败即丢当根——查 Valkey；bar 明细见 bar_hub 表对账。"},
    "hub.lease-lost":   {"label": "hub 租约丢失", "guide": "另一实例在位或存储异常，实例已退出——systemd 接管；持续出现查是否双实例。"},
    "runtime.fatal":    {"label": "运行时致命退出", "guide": "事件线程死亡/看门狗类致命——systemd 自动重启；journalctl 归因首个异常。"},
    "runtime.guard":    {"label": "运行时守卫拦截", "guide": "handler 异常被守卫拦截（一次异常=永久失聪防线）——journalctl 看首个 traceback。"},
    # ——— 对账/交易域 ———
    "reconcile.error":  {"label": "三账对账异常", "guide": "对账台账有差异——到风控-对账处置台逐条处置。"},
    "reconcile.open-orders": {"label": "在场委托残留", "guide": "对账发现未成交在场委托——确认是否预期；非预期人工撤单。"},
    "reconcile.wal":    {"label": "WAL 残留", "guide": "订单时序日志残留未闭环——核对订单终态，处置台账。"},
    "risk.halt-edge":   {"label": "熔断沿撤单", "guide": "进入熔断瞬间已撤全部在场委托——查熔断归因（风控面板）。"},
    # ——— 数据/同步 ———
    "data.disconn":     {"label": "数据断连", "guide": "行情/数据源连接断开——查 dataops 数据源页与适配器状态。"},
    "data.adj-degrade": {"label": "复权因子降级", "guide": "积分不足复权接口降级——跨除权日因子暂不可用；积分到账后触发回补。"},
    "sync.status":      {"label": "同步状态", "guide": "数据同步完成/失败状态——dataops 同步日志归因。"},
    "minute.gap":       {"label": "分钟数据漏取", "guide": "腾讯分钟攒漏取（bar_1min 落后昨天）——查 tencent_minute sync_log；腾讯 1min 滚动窗口漏一天断 ~4h，人工回补。"},
    "disk.warning":     {"label": "磁盘告警", "guide": "磁盘余量触警——清理 var/log 与旧 release（GC 保 N=5）。"},
    # ——— 健康监控 ———
    "health.iface-down":   {"label": "接口健康异常", "guide": "LLM/PG/Valkey 有离线——查 /observe 健康页与对应服务。"},
    "health.channel-down": {"label": "通道连通异常", "guide": "数据/通知通道离线——查 dataops 数据源页与通道配置。"},
    "health.schema-drift": {"label": "schema 漂移", "guide": "列级校验发现缺失列——核对迁移是否全跑（alembic current）。"},
    "health.schema-off":   {"label": "schema 校验禁用", "guide": "校验开关被关——确认是否有意，防漂移盲区。"},
    "health.recovery":     {"label": "健康恢复", "guide": "组件恢复正常（信息性）。"},
    "health.component":    {"label": "组件阈值告警", "guide": "健康组件指标越限——/observe 健康页看趋势归因。"},
    # ——— 平台杂项 ———
    "task.failed":      {"label": "后台任务失败", "guide": "任务管理器登记失败——任务列表看错误详情，重试或修配置。"},
    "llm.budget":       {"label": "LLM 预算预警", "guide": "用量接近预算上限——设置页调额或收敛调用方。"},
    "email.failed":     {"label": "邮件最终失败", "guide": "发件箱最终失败——查 SMTP 配置与收件域投递链（发件箱页有 last_error）。"},
    "factor.drift":     {"label": "因子漂移", "guide": "实盘 vs 回测因子偏差超限——核对数据口径与因子参数。"},
}
