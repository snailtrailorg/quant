// verify-gate.mjs —— 验证门拦截验证：短区间回测(<90天) → 标记验证 → 断言「证据不足」拦截文案
// 用法：cd web && SMOKE_PASS=xxx node verify-gate.mjs
import puppeteer from 'puppeteer-core'

const BASE = 'https://quant.snailtrail.cc'
const PASS = process.env.SMOKE_PASS
if (!PASS) { console.error('需 SMOKE_PASS 环境变量'); process.exit(2) }

const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
await p.evaluateOnNewDocument(() => { Object.defineProperty(navigator, 'language', { get: () => 'zh-CN', configurable: true }); Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'en'], configurable: true }) })
p.on('pageerror', e => console.log('[PAGEERROR]', String(e).slice(0, 150)))

await p.goto(`${BASE}/login`, { waitUntil: 'networkidle2' })
await p.type('input', 'admin'); const inputs = await p.$$('input'); await inputs[1].type(PASS)
await p.click('button[type=button], .el-button--primary')
await p.waitForNavigation({ waitUntil: 'networkidle2' }).catch(() => {})
await new Promise(r => setTimeout(r, 1000))

const api = (path, opt) => p.evaluate(async (u, o) => {
  const r = await fetch(u, { headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + localStorage.getItem('token') }, ...o })
  return { status: r.status, body: await r.json().catch(() => ({})) }
}, `${BASE}${path}`, opt)

// 策略 + 持仓标的
const strat = await api('/api/strategy'); const pos = await api('/api/position')
const strategies = Array.isArray(strat.body) ? strat.body : []
const syms = ((pos.body || {}).positions || []).map(x => x.symbol).filter(Boolean).slice(0, 2)
if (!strategies.length || !syms.length) { console.log('✗ 无策略或标的'); await b.close(); process.exit(2) }

// 短区间回测（30 天 < 90 天门槛）
const payload = { strategy_config_id: strategies[0].id, symbols: syms, pool_id: null, mode: 'parallel', params: { capital: 1000000, commission: 5 / 10000, start: '2026-08-01', end: '2026-08-30' } }
const created = await api('/api/backtest', { method: 'POST', body: JSON.stringify(payload) })
const runId = created.body?.run_id
console.log(`发起短区间回测 run=${runId} symbols=${syms.join(',')} 区间 30 天`)

// 轮询 done
let done = false
for (let i = 0; i < 45; i++) {
  await new Promise(r => setTimeout(r, 2000))
  const d = await api(`/api/backtest/${runId}`)
  if (d.body?.status === 'done') { done = true; break }
  if (d.body?.status === 'failed' || d.body?.status === 'error') { console.log('回测终态异常:', d.body?.status); break }
}
if (!done) { console.log('✗ 回测未 done'); await b.close(); process.exit(2) }

// 打开 BacktestRun 页，点「标记回测验证」
await p.goto(`${BASE}/backtest/${runId}`, { waitUntil: 'networkidle2' }); await new Promise(r => setTimeout(r, 1200))
const clicked = await p.evaluate(() => { const e = [...document.querySelectorAll('button')].find(b => b.textContent.includes('标记回测验证')); e?.click(); return !!e })
await new Promise(r => setTimeout(r, 800))
const warn = await p.evaluate(() => document.querySelector('.el-message--warning .el-message__content')?.textContent?.trim() ?? '')
console.log(`标记按钮点击=${clicked}`)
console.log(`拦截文案=${warn}`)
const ok = warn.includes('证据不足')
console.log(ok ? '✓ 验证门拦截生效' : '✗ 验证门拦截未生效')
await b.close()
process.exit(ok ? 0 : 1)
