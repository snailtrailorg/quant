/**
 * el-table-v2 列宽估宽（批16 v2：M3 机制——v2 必须显式数字 width，无 v1 的"不设自动 flex"语义）。
 *
 * 纪律（盲审 B-P1-7）：
 * - 采样对**渲染后文本**估宽（枚举经 label 映射成中文后的宽度），不能拿原始 dataKey 值
 * - 末列弹性 = flexGrow: 1 + minWidth（不是"不设 width"——v2 会塌）
 * - 采样取前 50 行防大表卡顿
 */

/** 单值显示宽度（px）估算：中文/全角 ≈15px，ASCII ≈8px（13px 字号近似） */
export const textPx = (v) => {
  const s = v === null || v === undefined ? '' : String(v)
  let w = 0
  for (const ch of s) w += ch.charCodeAt(0) > 127 ? 15 : 8
  return w
}

/**
 * 估一列宽度：表头宽与采样内容宽取大 + padding（+sortable 再 +20）。
 * @param header  表头文案
 * @param values  采样值数组（渲染后文本优先；原始值亦可，textPx 自适配）
 * @param opts    { sortable, padding, cap }
 */
export const estColWidth = (header, values = [], opts = {}) => {
  const { sortable = false, padding = 24, cap = 360 } = opts
  let content = textPx(header)
  for (const v of values.slice(0, 50)) content = Math.max(content, textPx(v))
  const w = content + padding + (sortable ? 20 : 0)
  return Math.min(Math.max(w, 80), cap)
}

/** 从行数组取某字段采样（默认前 50 行） */
export const sample = (rows, key, n = 50) =>
  (rows || []).slice(0, n).map(r => r?.[key]).filter(v => v !== null && v !== undefined)
