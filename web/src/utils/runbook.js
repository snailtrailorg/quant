// runbook.js —— 通知 code → 结构化处置映射（web 长尾批 2026-09-01，15号复审遗留）
// 机制：后端 notify(code=...) 渐进打码；未映射 code 前端只显 chip 不报错。
// 映射表 = code → { label, guide }；guide 为一句话处置（自包含，不依赖外链）。
export const RUNBOOK = {
  'l3.failed':        { label: 'L3 拉起失败', guide: 'systemctl start 非零退出——查 journalctl -u <unit> 与 StartLimit 窗口；L3 会按 300s 周期重试。' },
  'l3.pull':          { label: 'L3 自动拉起', guide: '期望源在而 systemd 无实例——若非预期，先在 Web 停任务或打 hub 维护标记。' },
  'sa4.restart':      { label: 'SA4 自动重启', guide: '崩溃退避拉起中；连续出现查该单元 journalctl 归因首个异常。' },
  'deps.exhausted':   { label: '依赖探活耗尽', guide: 'PG 持续不可达超 10 分钟——查 PG 服务与网络；runner 已退出待 systemd 重试。' },
  'frozen.intercept': { label: '冻结期拦截 BUY', guide: '不可信 bar/流 gap（数据污染事实）——重启任务解冻；SELL 不受限。' },
  'buy.blocked':      { label: 'BUY 拦截(数据不新鲜)', guide: 'bar 停更>300s 或 hub 心跳丢失——查 hub 状态与行情流；恢复后自动放行。' },
  'reconcile.error':  { label: '三账对账异常', guide: '对账台账有差异——到风控-对账处置台逐条处置。' },
  'factor.drift':     { label: '因子漂移', guide: '实盘 vs 回测因子偏差超限——核对数据口径与因子参数。' },
  'health.iface-down':{ label: '接口健康异常', guide: 'LLM/PG/Valkey 有离线——查 /observe 健康页与对应服务。' },
  'health.channel-down': { label: '通道连通异常', guide: '数据/通知通道离线——查 dataops 数据源页与通道配置。' },
}

export const runbookOf = code => (code && RUNBOOK[code]) || null
