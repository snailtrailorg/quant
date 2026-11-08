// 批 171o：紧急处置页三纯函数（可测——B-P2-3 建议落 utils）
// selectedIds：勾选集 id 列表（默认全选语义在页面态；此处只做投影）
export const selectedIds = accounts => (accounts || []).filter(a => a.checked).map(a => a.account_id)

// canResume：恢复钮置灰判定——熔断持续 <300s 置灰（防手滑连环 halt→秒 resume）
// haltTs：服务器 halt_ts epoch 秒（A-P1-2 服务器时钟权威）；now：客户端当前 epoch
export const canResume = (halted, haltTs, now) => {
  if (!halted) return false          // 未熔断无恢复语义
  if (!haltTs) return true           // 键缺失（老熔断无 ts）——容错放行
  return (now - haltTs) >= 300
}

// haltDurationText：已持续 mm:ss（状态条展示）
export const haltDurationText = (haltTs, now) => {
  if (!haltTs) return '—'
  const s = Math.max(0, Math.floor(now - haltTs))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
