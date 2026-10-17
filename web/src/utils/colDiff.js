// 批126h：列配置偏离计数（ColumnSettings 蓝点/恢复默认(N) 的判定核心）。
// 纯函数抽出（用户裁定补单测）——组件只做渲染，判定可独立验证：
// 对称差=默认列集删 N 列 + 开出 M 个默认隐藏列，N+M=偏离度（0=默认态）。
export function colOffCount(defaultKeys, visibleKeys) {
  const d = new Set(defaultKeys || [])
  const v = new Set(visibleKeys || [])
  let n = 0
  d.forEach(k => { if (!v.has(k)) n++ })
  v.forEach(k => { if (!d.has(k)) n++ })
  return n
}

// 偏离判定（蓝点显隐）：offCount>0 即亮
export function isColOff(defaultKeys, visibleKeys) {
  return colOffCount(defaultKeys, visibleKeys) > 0
}
