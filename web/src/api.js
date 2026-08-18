import axios from 'axios'
import i18n from './i18n'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

// 请求拦截：带 token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：401 跳登录
api.interceptors.response.use(
  res => res.data,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err.response?.data || err)
  }
)

// 错误码本地化：后端返回 code 时显示 err.<CODE> 翻译（N 语言），无映射回落 detail/消息
export function apiErr(e, fallback = '') {
  const g = i18n.global
  if (e?.code && g.te('err.' + e.code)) return g.t('err.' + e.code)
  return e?.detail || e?.message || fallback
}

export default api

export const login = (username, password) =>
  api.post('/auth/login', { username, password })

export const getMe = () => api.get('/auth/me')

export const getDataIntegrity = freq => api.get('/data-integrity', { params: { freq } })

export const getDataSourceUsage = () => api.get('/data-source-usage')

export const getStrategies = () => api.get('/strategy')
export const startStrategy = id => api.post(`/strategy/${id}/start`)
export const stopStrategy = id => api.post(`/strategy/${id}/stop`)

export const getAstockSelection = date => api.get('/astock/selection', { params: { date } })

export const getRiskState = () => api.get('/risk/state')
export const riskHalt = () => api.post('/risk/halt')
export const riskResume = () => api.post('/risk/resume')

export const getAudit = () => api.get('/audit')
export const getUsers = () => api.get('/user')
export const createUser = (username, password, role) =>
  api.post('/user', { username, password, role })

export const getAccounts = () => api.get('/account')
export const getLogs = () => api.get('/log')
export const getEmailOutbox = () => api.get('/email-outbox')
export const getNotifications = (status = 'active', limit = 50) => api.get('/notifications', { params: { status, limit } })
export const ackAllNotifications = () => api.post('/notifications/ack-all')
export const getSmtpConfig = () => api.get('/smtp-config')
export const saveSmtpConfig = (data) => api.put('/smtp-config', data)
export const sendTestEmail = (data) => api.post('/email/test', data)
export const chat = message => api.post('/chat', { message })

export const getLLMModels = () => api.get('/llm-models')
export const createLLMModel = (data) => api.post('/llm-models', data)
export const updateLLMModel = (id, data) => api.put(`/llm-models/${id}`, data)
export const deleteLLMModel = (id) => api.delete(`/llm-models/${id}`)
export const testLLMModel = (id) => api.post(`/llm-models/${id}/test`)
export const getLLMUsage = () => api.get('/llm-usage/summary')

export const getDataSources = () => api.get('/data-sources')
export const createDataSource = (data) => api.post('/data-sources', data)
export const updateDataSource = (id, data) => api.put(`/data-sources/${id}`, data)
export const deleteDataSource = (id) => api.delete(`/data-sources/${id}`)
export const testDataSource = (id) => api.post(`/data-sources/${id}/test`)

export const getTasks = (status) => api.get('/tasks', { params: status ? { status } : {} })
export const getBacktests = () => api.get('/backtest')
export const createBacktest = (data) => api.post('/backtest', data)
export const getBacktestRun = (runId) => api.get(`/backtest/${runId}`)
export const getPools = () => api.get('/pool')
export const createPoolApi = (data) => api.post('/pool', data)
export const deletePoolApi = (id) => api.delete(`/pool/${id}`)
export const getBacktestStream = (runId, symbol) => api.get(`/backtest/${runId}/${symbol}/stream`)
export const getTaskDetail = (id) => api.get(`/tasks/${id}`)
export const terminateTask = (id) => api.post(`/tasks/${id}/terminate`)
export const forceDeleteTask = (id) => api.post(`/tasks/${id}/force-delete`)
export const detectStuck = () => api.post('/tasks/detect-stuck')

export const getChannels = () => api.get('/channels')
export const createChannel = (data) => api.post('/channels', data)
export const updateChannel = (id, data) => api.put(`/channels/${id}`, data)
export const deleteChannel = (id) => api.delete(`/channels/${id}`)
export const testChannel = (id) => api.post(`/channels/${id}/test`)

export const getBrokers = () => api.get('/brokers')
export const createBroker = (data) => api.post('/brokers', data)
export const updateBroker = (id, data) => api.put(`/brokers/${id}`, data)
export const deleteBroker = (id) => api.delete(`/brokers/${id}`)
export const testBroker = (id) => api.post(`/brokers/${id}/test`)

export const getRiskRules = () => api.get('/risk-rules')
export const getRiskRuleTypes = () => api.get('/risk-rules/types')
export const createRiskRule = (data) => api.post('/risk-rules', data)
export const updateRiskRule = (id, data) => api.put(`/risk-rules/${id}`, data)
export const deleteRiskRule = (id) => api.delete(`/risk-rules/${id}`)

export const getFeishuList = () => api.get('/feishu/list')
export const feishuConnect = () => api.post('/feishu/connect')
export const feishuUpdate = (id, data) => api.put(`/feishu/${id}`, data)
export const feishuStatus = (sid) => api.get(`/feishu/status/${sid}`)
export const feishuStart = (id) => api.post(`/feishu/${id}/start`)
export const feishuStop = (id) => api.post(`/feishu/${id}/stop`)
export const feishuDelete = (id) => api.delete(`/feishu/${id}`)
export const testFeishu = (id) => api.post(`/feishu/${id}/test`)

export const getHealth = () => api.get('/health')
export const getHealthComponents = () => api.get('/health/components')
export const getHealthEvents = (limit = 100) => api.get('/health/events', { params: { limit } })
export const verifyStrategy = id => api.post(`/strategy/${id}/verify`)
export const createStrategy = data => api.post('/strategy', data)
export const getFactorList = () => api.get('/factors')
export const getReconcile = () => api.get('/reconcile')
export const getDashboard = () => api.get('/dashboard')
export const updateStrategy = (id, data) => api.put(`/strategy/${id}`, data)
export const validatePythonCode = code => api.post('/strategy/validate-python', { code })
export const validateParams = (parameter_defs, params) => api.post('/strategy/validate-params', { parameter_defs, params })
export const validateFactorCode = (code, name) => api.post('/factors/validate', { code, name })
export const createFactor = data => api.post('/factors', data)
export const updateFactor = (name, data) => api.put(`/factors/${name}`, data)
export const deleteFactor = name => api.delete(`/factors/${name}`)

// 实盘任务（live_task，策略与标的分离）
export const getLiveTasks = (status) => api.get('/live-task', { params: status ? { status } : {} })
export const createLiveTask = data => api.post('/live-task', data)
export const startLiveTask = id => api.post(`/live-task/${id}/start`)
export const stopLiveTask = id => api.post(`/live-task/${id}/stop`)
export const deleteLiveTask = id => api.delete(`/live-task/${id}`)

// 系统配置（admin 可改，celery_concurrency 支持动态生效）
export const getSystemConfig = () => api.get('/system-config')
export const updateSystemConfig = (key, value) => api.put(`/system-config/${key}`, { value })

export const getSyncConfigs = () => api.get('/sync/config')
export const updateSyncConfig = (id, data) => api.put('/sync/config/' + id, data)
export const triggerSync = id => api.post('/sync/trigger/' + id)
export const getSyncLogs = () => api.get('/sync/log')
export const deleteSyncData = id => api.delete('/sync/data/' + id)

// 持仓/订单/盈亏（Trading 看板）
export const getPosition = () => api.get('/position')
export const getOrders = () => api.get('/orders')
export const getPnl = () => api.get('/pnl')

// 邀请制用户管理
export const inviteUser = (email, lang) => api.post('/auth/invite', { email, lang })
export const getInvites = () => api.get('/invites')
export const revokeInvite = id => api.post(`/invites/${id}/revoke`)
export const verifyInviteToken = token => api.get(`/auth/invite/verify`, { params: { token } })
export const registerUser = (token, username, password, lang) => api.post('/auth/register', { token, username, password, lang })
export const forgotPassword = (email, lang) => api.post('/auth/forgot-password', { email, lang })
export const resetPassword = (token, new_password) => api.post('/auth/reset-password', { token, new_password })
export const changePassword = (old_password, new_password) => api.post('/auth/change-password', { old_password, new_password })
export const getTerms = () => api.get('/terms')
export const getLiveTrading = api.get('/live-trading')
export const updateLiveTrading = (market, enabled) => api.put(`/live-trading/${market}?enabled=${enabled}`)
export const logAnalyze = (data) => api.post('/log/analyze', data)
export const logoutApi = () => api.post('/auth/logout')
