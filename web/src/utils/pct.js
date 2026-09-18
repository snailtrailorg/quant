// 批45：百分比显示/编辑换算单源（存储 0~1 不动，显示层 ×100——两页共用：RunConfig/RiskRules）
// 浮点坑：0.07*100=7.000000000000001——显示/初值都归整
export const toPct = v => (v == null ? null : Number((v * 100).toFixed(2)))   // 编辑模型初值（0.9→90）
export const pctShow = v => (v == null ? '' : `${Math.round(v * 100)}%`)      // 纯显示（0.9→"90%"）
export const fromPct = v => (v == null ? null : v / 100)                      // 提交回存储（91→0.91）
export const camelKey = k => k.replace(/_([a-z])/g, (_, c) => c.toUpperCase())   // 词条键转换（与 riskRule.param 同款，单源防漂移——盲审 A-P2-3）
