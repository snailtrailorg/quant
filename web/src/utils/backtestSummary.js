// wd-20 §1.3 · 回测成绩单键归一（消灭列表/策略页/首页三处各自拼写）
// 旧数据（回填前）summary_metrics 为空 → 各值 null，消费方显示 '—'
export const bs = (run) => {
  const m = run?.summary_metrics || {}
  return {
    ret: m.total_return_pct ?? null,
    dd: m.max_drawdown_pct ?? null,
    sharpe: m.sharpe ?? null,
    win: m.win_rate ?? null,
    n: m.trade_count ?? run?.total_trades ?? null,
  }
}
export const pct = (v, digits = 1) => (v == null ? '—' : `${v.toFixed(digits)}%`)
