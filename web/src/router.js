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

      // 交易
      { path: 'trading', name: 'trading', component: () => import('./views/Trading.vue') },
      { path: 'monitoring', name: 'monitoring', component: () => import('./views/Monitoring.vue') },

      // 策略
      { path: 'strategy', name: 'strategy', component: () => import('./views/Strategy.vue') },
      { path: 'backtest', name: 'backtest', component: () => import('./views/Backtest.vue') },
      { path: 'pool', name: 'pool', component: () => import('./views/Pool.vue') },
      { path: 'factors', name: 'factors', component: () => import('./views/Factors.vue') },

      // 分析
      { path: 'analysis', name: 'analysis', component: () => import('./views/Analysis.vue') },
      { path: 'ascreen', name: 'ascreen', component: () => import('./views/AScreen.vue') },
      { path: 'cbscreen', name: 'cbscreen', component: () => import('./views/CBScreen.vue') },
      { path: 'etfscreen', name: 'etfscreen', component: () => import('./views/ETFScreen.vue') },
      { path: 'chat', name: 'chat', component: () => import('./views/AIChat.vue') },

      // 风控
      { path: 'risk', name: 'risk', component: () => import('./views/Risk.vue') },
      { path: 'reconcile', name: 'reconcile', component: () => import('./views/Reconcile.vue') },

      // 系统 (Admin)
      { path: 'account', name: 'account', component: () => import('./views/Account.vue'), meta: { admin: true } },
      { path: 'health', name: 'health', component: () => import('./views/Health.vue'), meta: { admin: true } },
      { path: 'logs', name: 'logs', component: () => import('./views/Logs.vue'), meta: { admin: true } },
      { path: 'data-manage', name: 'data-manage', component: () => import('./views/DataManage.vue'), meta: { admin: true } },
      { path: 'data-manage/:syncId', name: 'symbol-manage', component: () => import('./views/SymbolManage.vue'), meta: { admin: true } },
      { path: 'audit', name: 'audit', component: () => import('./views/Audit.vue'), meta: { admin: true } },
      { path: 'llm-models', name: 'llm-models', component: () => import('./views/LLMModels.vue'), meta: { admin: true } },
      { path: 'data-sources', name: 'data-sources', component: () => import('./views/DataSources.vue'), meta: { admin: true } },
      { path: 'tasks', name: 'tasks', component: () => import('./views/TaskManager.vue') },
      { path: 'channels', name: 'channels', component: () => import('./views/Channels.vue'), meta: { admin: true } },
      { path: 'brokers', name: 'brokers', component: () => import('./views/Brokers.vue'), meta: { admin: true } },
      { path: 'risk-rules', name: 'risk-rules', component: () => import('./views/RiskRules.vue'), meta: { admin: true } },
      { path: 'feishu', name: 'feishu', component: () => import('./views/FeishuConnect.vue'), meta: { admin: true } },
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