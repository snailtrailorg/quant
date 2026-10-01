// 批24 迭代七（用户裁定）：功能权限分组——展示层结构（文案在 perm.grp_*/perm.key_* 词条）。
// 键集后端单源下发（GET /permissions r.keys），此处只做组映射；后端新增键前端未收录时落 other 兜底组。
// 分组与侧栏菜单分区（实盘交易/策略研究/风险控制/系统管理）一一对应——文案师裁定（交易员看权限即知"哪个区的钥匙"）。
export const PERM_GROUPS = [
  { id: 'basic', keys: ['read'] },
  { id: 'trading', keys: ['trade', 'halt', 'resume', 'live_trading_control'] },
  { id: 'strategy', keys: ['strategy_control', 'data_sync'] },
  { id: 'risk', keys: ['risk_rules'] },
  { id: 'system', keys: ['system_config', 'user_mgmt', 'llm_config', 'im_bots_config', 'alerts_config'] },
]

// keys=后端下发的全键集；返回分组列表（只含有键的组）+未知键 other 兜底
export const permGroupsOf = keys => {
  const known = new Set(PERM_GROUPS.flatMap(g => g.keys))
  const groups = PERM_GROUPS
    .map(g => ({ id: g.id, keys: g.keys.filter(k => keys.includes(k)) }))
    .filter(g => g.keys.length)
  const rest = keys.filter(k => !known.has(k))
  if (rest.length) groups.push({ id: 'other', keys: rest })
  return groups
}
