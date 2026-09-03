// wd-20 §2.6 · 告警类别 → 目标路由映射（Dashboard 告警条与铃铛共用——消灭盲跳 /live-task）
export const goCategoryPath = c => ({
  email: '/settings?tab=run',
  task: '/dataops?tab=sched',
  risk: '/risk',
  data: '/dataops?tab=integrity',
  system: '/observe?tab=health',
}[c] || '/')
