// seed-backtest.mjs —— 冒烟门配套造数：发起一次真实回测，验证成绩单三处真数字 + 验证门
// 用法：cd web && SMOKE_PASS=xxx node seed-backtest.mjs
import puppeteer from 'puppeteer-core'

const BASE = 'https://quant.snailtrail.cc'
const PASS = process.env.SMOKE_PASS
if (!PASS) { console.error('需 SMOKE_PASS 环境变量'); process.exit(2) }

const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
await p.evaluateOnNewDocument(() => { Object.defineProperty(navigator, 'language', { get: () => 'zh-CN', configurable: true }); Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'en'], configurable: true }) })
p.on('pageerror', e => console.log('[PAGEERROR]', String(e).slice(0, 150)))

// 登录
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle2' })
await p.type('input', 'admin')
const inputs = await p.$$('input'); await inputs[1].type(PASS)
await p.click('button[type=button], .el-button--primary')
await p.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {})
await new Promise(r => setTimeout(r, 1000))

// 浏览器内 fetch（token 自动带）
const api = (path, opt) => p.evaluate(async (u, o) => {
  const r = await fetch(u, { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('token') }, ...o })
  return { status: r.status, body: await r.json().catch(() => ({})) }
}, `${BASE}${path}`, opt)

// 探查策略 + 持仓标的
const strat = await api('/api/strategy')
const pos = await api('/api/position')
console.log('策略:', JSON.stringify((strat.body || []).map(s => ({ id: s.id, name: s.name, verified: s.backtest_verified }))))
console.log('持仓标的:', JSON.stringify(((pos.body || {}).positions || []).map(x => x.symbol).slice(0, 6)))

const strategies = Array.isArray(strat.body) ? strat.body : []
if (!strategies.length) { console.log('✗ 无策略，无法造数'); await b.close(); process.exit(2) }

const sid = strategies[0].id
const symbols = ((pos.body || {}).positions || []).map(x => x.symbol).filter(Boolean).slice(0, 3)
const sym = symbols.length ? symbols : ['600000.SH']
const payload = {
  strategy_config_id: sid,
  symbols: sym,
  pool_id: null,
  mode: 'parallel',
  params: { capital: 1000000, commission: 5 / 10000, start: '2026-01-01', end: '2026-08-31' },
}
console.log(`发起回测: strategy=${sid} symbols=${sym.join(',')}`)
const created = await api('/api/backtest', { method: 'POST', body: JSON.stringify(payload) })
console.log('POST /backtest →', created.status, JSON.stringify(created.body).slice(0, 200))

// 轮询直到 done/failed（最多 90s）
let run = null
for (let i = 0; i < 45; i++) {
  await new Promise(r => setTimeout(r, 2000))
  const list = await api('/api/backtest')
  const runs = Array.isArray(list.body) ? list.body : (list.body?.runs || [])
  // 盲审 P1：列表端点返回 strategy_config_id（非 strategy_id），原 strategy_id 恒 undefined→永不命中
  run = runs.find(r => r.strategy_config_id === sid && (r.status === 'done' || r.status === 'failed'))
  if (run) break
}
if (run) {
  console.log(`回测终态: status=${run.status} id=${run.id}`)
  const detail = await api(`/api/backtest/${run.id}`)
  console.log('成绩单 summary_metrics:', JSON.stringify((detail.body?.summary_metrics) || (detail.body?.summary) || null))
  console.log('验证门字段 span_days/total_trades:', JSON.stringify(detail.body?.span_days ?? null), JSON.stringify(detail.body?.total_trades ?? null))
} else {
  console.log('✗ 90s 内未到终态（可能仍在 pending/running）')
  await b.close()
  process.exit(1)   // 盲审 P1：未到终态必须判红，防假绿
}
await b.close()
