// wd-20 §2.6 · 时间格式三态归一（消灭全站 slice 拼接各自的拼写）
//   fmtTime.s(ts)   → 'MM-DD HH:mm'        常规（列表/卡片）
//   fmtTime.t(ts)   → 'HH:mm:ss'           盘中（实时心跳/行情时间）
//   fmtTime.full(ts)→ 'YYYY-MM-DD HH:mm:ss' 日志/审计
// 入参兼容 'YYYY-MM-DDTHH:mm:ss' / 'YYYY-MM-DD HH:mm:ss' / Date
const norm = (ts) => {
  if (!ts) return ''
  const d = ts instanceof Date ? ts : new Date(String(ts).replace('T', ' ').includes('Z') ? ts : String(ts).replace('T', ' '))
  return isNaN(d.getTime()) ? String(ts) : d
}
const p2 = (n) => String(n).padStart(2, '0')

export const fmtTime = {
  s: (ts) => { const d = norm(ts); return d instanceof Date ? `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}` : d },
  t: (ts) => { const d = norm(ts); return d instanceof Date ? `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}` : d },
  full: (ts) => { const d = norm(ts); return d instanceof Date ? `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}` : d },
}
