import axios from 'axios'

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

export default api

export const login = (username, password) =>
  api.post('/auth/login', { username, password })

export const getMe = () => api.get('/auth/me')

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
export const getAlerts = () => api.get('/alert')
export const chat = message => api.post('/chat', { message })

export const getLLMModels = () => api.get('/llm-models')
export const createLLMModel = (data) => api.post('/llm-models', data)
export const updateLLMModel = (id, data) => api.put(`/llm-models/${id}`, data)
export const deleteLLMModel = (id) => api.delete(`/llm-models/${id}`)
export const testLLMModel = (id) => api.post(`/llm-models/${id}/test`)

export const getFeishuList = () => api.get('/feishu/list')
export const feishuConnect = () => api.post('/feishu/connect')
export const feishuUpdate = (id, data) => api.put(`/feishu/${id}`, data)
export const feishuStatus = (sid) => api.get(`/feishu/status/${sid}`)
export const feishuStart = (id) => api.post(`/feishu/${id}/start`)
export const feishuStop = (id) => api.post(`/feishu/${id}/stop`)
export const feishuDelete = (id) => api.delete(`/feishu/${id}`)
export const testFeishu = (id) => api.post(`/feishu/${id}/test`)

export const getHealth = () => api.get('/health')
export const getPools = () => api.get('/pool')
export const createPool = data => api.post('/pool', data)
export const verifyStrategy = id => api.post(`/strategy/${id}/verify`)
export const createStrategy = data => api.post('/strategy', data)
export const updateStrategy = (id, data) => api.put(`/strategy/${id}`, data)

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
export const inviteUser = email => api.post('/auth/invite', { email })
export const verifyInviteToken = token => api.get(`/auth/invite/verify`, { params: { token } })
export const registerUser = (token, username, password) => api.post('/auth/register', { token, username, password })
export const forgotPassword = email => api.post('/auth/forgot-password', { email })
export const resetPassword = (token, new_password) => api.post('/auth/reset-password', { token, new_password })
export const changePassword = (old_password, new_password) => api.post('/auth/change-password', { old_password, new_password })
