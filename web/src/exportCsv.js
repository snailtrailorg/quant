// 批24 迭代十六：CSV 导出共享 util（从 Audit.vue 批23 实现抽取——四日志页签通用）
// headers=表头文案数组；rows=已过滤后的可见行（二维数组，与 headers 对齐）
export const exportCsv = (filename, headers, rows) => {
  const esc = v => `"${(v ?? '').toString().replace(/"/g, '""')}"`
  const csv = '﻿' + [headers.map(esc).join(','), ...rows.map(r => r.map(esc).join(','))].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${filename}_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
