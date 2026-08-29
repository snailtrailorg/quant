// P3-10（web-design 04 §3/01 G4/G7/G10）：全局基础件——数字格式化 + 枚举中文化 + 证券代码展示。
// 全站消费的单一 util 层；各页逐步替换散落的 toFixed/裸枚举。

/** 千分位 + 中文单位：1000000000 → '10.00亿'（04 §3.1） */
export const fmtCn = (v, digits = 2) => {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e12) return (n / 1e12).toFixed(digits) + '万亿'
  if (abs >= 1e8) return (n / 1e8).toFixed(digits) + '亿'
  if (abs >= 1e4) return (n / 1e4).toFixed(digits) + '万'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

/** 千分位（股数等整数）：4996546 → '4,996,546' */
export const fmtInt = v => {
  const n = Number(v)
  return isFinite(n) ? n.toLocaleString('zh-CN') : '—'
}

/** 百分比带符号着色语义：0.0124 → '+1.24%'（04 §3.2：带符号显示） */
export const fmtPct = (v, digits = 2) => {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(digits)}%`
}

/** 价格：默认 2 位（可转债 3 位传 digits=3） */
export const fmtPrice = (v, digits = 2) => {
  const n = Number(v)
  return isFinite(n) ? n.toFixed(digits) : '—'
}

/** 空值治理（04 §3.5）：null/undefined/NaN → '—'（禁止 '缺'/null 上屏） */
export const dash = v => (v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v))) ? '—' : v

/** 证券代码展示：'600000.SHSE' → '600000'（内部命名空间不裸露给用户，04 §3.3） */
export const fmtCode = sym => dash(String(sym ?? '').split('.')[0])

/** 枚举中文化映射表（01 G10：SELL/running/complete… 不裸英文上屏） */
export const ENUM_ZH = {
  BUY: '买入', SELL: '卖出',
  running: '运行中', stopped: '已停止', pending: '等待中', error: '错误', frozen: '冻结',
  done: '完成', failed: '失败',
  submitted: '已报', part_filled: '部成', filled: '已成', cancelled: '已撤',
  approve: '放行', reject: '拒单', adjust: '覆写',
}
export const enumZh = k => ENUM_ZH[k] ?? dash(k)
