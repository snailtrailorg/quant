import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/login', name: 'login', component: () => import('./views/Login.vue') },
  { path: '/register', name: 'register', component: () => import('./views/Register.vue') },
  { path: '/forgot-password', name: 'forgot-password', component: () => import('./views/ForgotPassword.vue') },
  { path: '/reset-password', name: 'reset-password', component: () => import('./views/ResetPassword.vue') },
  {
    path: '/',
    component: () => import('./layouts/MainLayout.vue'),
    children: [
      // 总览
      { path: '', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
      { path: 'profile', name: 'profile', component: () => import('./views/Profile.vue') },

      // 交易
      { path: 'trading', name: 'trading', component: () => import('./views/Trading.vue') },
      { path: 'monitoring', redirect: '/live-task' },

      // 策略
      { path: 'strategy', name: 'strategy', component: () => import('./views/Strategy.vue') },
      { path: 'strategy/:id/edit', name: 'strategyEdit', component: () => import('./views/StrategyEdit.vue') },
      { path: 'live-task', name: 'live-task', component: () => import('./views/LiveTask.vue') },
      { path: 'backtest', name: 'backtest', component: () => import('./views/Backtest.vue') },
      { path: 'backtest/:id', name: 'backtest-run', component: () => import('./views/BacktestRun.vue') },
      { path: 'backtest/:id/view/:symbol', name: 'backtest-view', component: () => import('./views/BacktestView.vue') },
      { path: 'pool', name: 'pool', component: () => import('./views/Pool.vue') },
      { path: 'factors', name: 'factors', component: () => import('./views/Factors.vue') },

      // 分析
      { path: 'analysis', name: 'analysis', component: () => import('./views/Analysis.vue') },
      { path: 'stock/:symbol', name: 'stock-detail', component: () => import('./views/StockDetail.vue') },
      { path: 'integrations', name: 'integrations', component: () => import('./views/Integrations.vue') },
      { path: 'dataops', name: 'dataops', component: () => import('./views/DataOps.vue') },
      { path: 'observe', name: 'observe', component: () => import('./views/Observe.vue') },
      { path: 'permissions', name: 'permissions', component: () => import('./views/Permissions.vue'), meta: { admin: true } },
      { path: 'settings', name: 'settings', component: () => import('./views/Settings.vue') },
      { path: 'screener', name: 'screener', component: () => import('./views/Screener.vue') },   // P2-8 三合一（旧三路由保留兼容直链）
      { path: 'ascreen', redirect: '/screener?tab=astock' },
      { path: 'cbscreen', redirect: '/screener?tab=cb' },
      { path: 'etfscreen', redirect: '/screener?tab=etf' },
      { path: 'chat', name: 'chat', component: () => import('./views/AIChat.vue') },

      // 风控
      { path: 'risk', name: 'risk', component: () => import('./views/Risk.vue') },
      { path: 'reconcile', name: 'reconcile', component: () => import('./views/Reconcile.vue') },

      // 系统 (Admin)
      { path: 'account', redirect: '/settings?tab=users' },
      { path: 'help', redirect: '/observe?tab=health' },
      { path: 'health', redirect: '/observe?tab=health' },
      { path: 'logs', redirect: '/observe?tab=logs' },
      { path: 'data-manage', redirect: '/dataops?tab=sync' },
      { path: 'data-integrity', redirect: '/dataops?tab=integrity' },
      { path: 'data-manage/:syncId', name: 'symbol-manage', component: () => import('./views/SymbolManage.vue'), meta: { admin: true } },
      { path: 'audit', redirect: '/observe?tab=audit' },
      { path: 'llm-models', redirect: '/integrations?tab=llm' },
      { path: 'data-sources', redirect: '/integrations?tab=sources' },
      { path: 'tasks', redirect: '/dataops?tab=sched' },
      { path: 'channels', redirect: '/integrations?tab=push' },
      { path: 'brokers', redirect: '/integrations?tab=brokers' },
      { path: 'risk-rules', name: 'risk-rules', component: () => import('./views/RiskRules.vue'), meta: { admin: true } },
      { path: 'im-bots', redirect: '/integrations?tab=im' },
      { path: 'feishu', redirect: '/im-bots' },   // 19 号批 2:旧路由重定向
      { path: 'system-config', redirect: '/settings?tab=run' },
    ],
  },
]

const router = createRouter({ history: createWebHistory(), routes })

// W5 nav 守卫:模块级 me promise 缓存(首航 await,刷新不丢;盲审 A-P1 时序方案)
import { getMe } from './api'
let mePromise = null
const meOnce = () => (mePromise ??= getMe().catch(() => { mePromise = null; return null }))   // 失败不粘缓存,下次重试
export const resetMeCache = () => { mePromise = null }

router.beforeEach(async (to, from, next) => {
  const token = localStorage.getItem('token')
  const publicPages = ['login', 'register', 'forgot-password', 'reset-password']
  if (!publicPages.includes(to.name) && !token) { next('/login'); return }
  if (to.meta?.admin && localStorage.getItem('role') !== 'admin') { next('/'); return }
  // nav 维:hidden 拒路由+readonly 放行(标志经 me 消费方读取)。nav=UI 提示层,
  // 执行面在 api 维(直连 API 不受 nav 限=设计)
  if (token && !publicPages.includes(to.name)) {
    const me = await meOnce()
    const nav = me?.nav || {}
    // 路由 path→菜单 id 映射(菜单 id=NAV_ITEMS 常量;route.path 去斜杠首段)
    const menuId = to.path.replace(/^\/+/, '').split('/')[0] || 'dashboard'
    const state = nav[menuId]
    if (state === 'hidden') { next('/'); return }
  }
  next()
})

export default router