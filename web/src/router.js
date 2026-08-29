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
      { path: 'monitoring', name: 'monitoring', component: () => import('./views/Monitoring.vue') },

      // 策略
      { path: 'strategy', name: 'strategy', component: () => import('./views/Strategy.vue') },
      { path: 'live-task', name: 'live-task', component: () => import('./views/LiveTask.vue') },
      { path: 'backtest', name: 'backtest', component: () => import('./views/Backtest.vue') },
      { path: 'backtest/:id', name: 'backtest-run', component: () => import('./views/BacktestRun.vue') },
      { path: 'backtest/:id/view/:symbol', name: 'backtest-view', component: () => import('./views/BacktestView.vue') },
      { path: 'pool', name: 'pool', component: () => import('./views/Pool.vue') },
      { path: 'factors', name: 'factors', component: () => import('./views/Factors.vue') },

      // 分析
      { path: 'analysis', name: 'analysis', component: () => import('./views/Analysis.vue') },
      { path: 'stock/:symbol', name: 'stock-detail', component: () => import('./views/StockDetail.vue') },
      { path: 'screener', name: 'screener', component: () => import('./views/Screener.vue') },   // P2-8 三合一（旧三路由保留兼容直链）
      { path: 'ascreen', name: 'ascreen', component: () => import('./views/AScreen.vue') },
      { path: 'cbscreen', name: 'cbscreen', component: () => import('./views/CBScreen.vue') },
      { path: 'etfscreen', name: 'etfscreen', component: () => import('./views/ETFScreen.vue') },
      { path: 'chat', name: 'chat', component: () => import('./views/AIChat.vue') },

      // 风控
      { path: 'risk', name: 'risk', component: () => import('./views/Risk.vue') },
      { path: 'reconcile', name: 'reconcile', component: () => import('./views/Reconcile.vue') },

      // 系统 (Admin)
      { path: 'account', name: 'account', component: () => import('./views/Account.vue'), meta: { admin: true } },
      { path: 'help', name: 'help', component: () => import('./views/Help.vue') },
      { path: 'health', name: 'health', component: () => import('./views/Health.vue'), meta: { admin: true } },
      { path: 'logs', name: 'logs', component: () => import('./views/Logs.vue'), meta: { admin: true } },
      { path: 'data-manage', name: 'data-manage', component: () => import('./views/DataManage.vue'), meta: { admin: true } },
      { path: 'data-integrity', name: 'data-integrity', component: () => import('./views/DataIntegrity.vue'), meta: { admin: true } },
      { path: 'data-manage/:syncId', name: 'symbol-manage', component: () => import('./views/SymbolManage.vue'), meta: { admin: true } },
      { path: 'audit', name: 'audit', component: () => import('./views/Audit.vue'), meta: { admin: true } },
      { path: 'llm-models', name: 'llm-models', component: () => import('./views/LLMModels.vue'), meta: { admin: true } },
      { path: 'data-sources', name: 'data-sources', component: () => import('./views/DataSources.vue'), meta: { admin: true } },
      { path: 'tasks', name: 'tasks', component: () => import('./views/TaskManager.vue') },
      { path: 'channels', name: 'channels', component: () => import('./views/Channels.vue'), meta: { admin: true } },
      { path: 'brokers', name: 'brokers', component: () => import('./views/Brokers.vue'), meta: { admin: true } },
      { path: 'risk-rules', name: 'risk-rules', component: () => import('./views/RiskRules.vue'), meta: { admin: true } },
      { path: 'im-bots', name: 'im-bots', component: () => import('./views/ImBots.vue'), meta: { admin: true } },
      { path: 'feishu', redirect: '/im-bots' },   // 19 号批 2:旧路由重定向
      { path: 'system-config', name: 'system-config', component: () => import('./views/SystemConfig.vue'), meta: { admin: true } },
    ],
  },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const publicPages = ['login', 'register', 'forgot-password', 'reset-password']
  if (!publicPages.includes(to.name) && !token) { next('/login'); return }
  if (to.meta?.admin && localStorage.getItem('role') !== 'admin') { next('/'); return }
  next()
})

export default router