// smoke-ui.mjs —— 前端运行时冒烟门（wd-20 收官日 #4，2026-09-03）
// puppeteer 登录 prod 走真 DOM 逐页断言（build+API 冒烟对 Vue 运行时错是双盲区——白屏/菜单两事故实证）。
// 用法：cd web && SMOKE_PASS=xxx node smoke-ui.mjs
// 退出码：0=全绿，1=有失败；pageerror/console.error 汇总在末尾。
import puppeteer from 'puppeteer-core'

const BASE = 'https://quant.snailtrail.cc'
const USER = process.env.SMOKE_USER || 'admin'
const PASS = process.env.SMOKE_PASS   // 生产 admin 密码，经环境变量注入，不入 repo
if (!PASS) { console.error('✗ 需 SMOKE_PASS 环境变量（生产 admin 密码）：SMOKE_PASS=xxx node smoke-ui.mjs'); process.exit(2) }

const results = []
const errors = []
const assert = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? '✓' : '✗'} ${name}${detail ? ' — ' + detail : ''}`) }
const skip = (name, detail = '') => { results.push({ name, ok: true, detail }); console.log(`○ ${name}${detail ? ' — ' + detail : ''}`) }

const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
p.on('console', m => { if (m.type() === 'error') errors.push(`[console.error] ${m.text().slice(0, 160)}`) })
p.on('pageerror', e => errors.push(`[PAGEERROR] ${String(e).slice(0, 200)}`))
p.on('requestfailed', r => { if (r.url().includes('quant.snailtrail.cc')) errors.push(`[请求失败] ${r.url().slice(-90)}`) })
p.on('response', r => { if (r.status() === 404 && r.url().includes('quant.snailtrail.cc')) errors.push(`[404] ${r.url().replace('https://quant.snailtrail.cc', '')}`) })

const nav = async (path) => { await p.goto(`${BASE}${path}`, { waitUntil: 'networkidle2', timeout: 30000 }).catch(() => {}); await new Promise(r => setTimeout(r, 700)) }
const count = sel => p.evaluate(s => document.querySelectorAll(s).length, sel)
const hasText = t => p.evaluate(txt => document.body.innerText.includes(txt), t)
const clickText = (t, sel = 'button') => p.evaluate(({ t, sel }) => { const e = [...document.querySelectorAll(sel)].find(x => x.textContent.includes(t)); e?.click(); return !!e }, { t, sel })

// ---- 登录 ----
try {
  await p.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 30000 })
  await p.type('input', USER)
  const inputs = await p.$$('input')
  if (inputs.length < 2) throw new Error('登录页 input 数异常')
  await inputs[1].type(PASS)
  await p.click('button[type=button], .el-button--primary')
  await p.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => {})
  await new Promise(r => setTimeout(r, 1200))
  assert('登录', !p.url().includes('login'), p.url())
} catch (e) { assert('登录', false, String(e).slice(0, 120)) }

// ---- 菜单壳 ----
try {
  const g = await count('.el-menu .el-sub-menu__title')
  assert('菜单组(应4)', g >= 4, `组数=${g}`)
} catch (e) { assert('菜单组', false, String(e).slice(0, 100)) }

// ---- 侧栏折叠交互 ----
try {
  const w0 = await p.evaluate(() => document.querySelector('.el-aside')?.style.width || getComputedStyle(document.querySelector('.el-aside')).width)
  await clickText('«', '.el-header button')
  await new Promise(r => setTimeout(r, 400))
  const w1 = await p.evaluate(() => document.querySelector('.el-aside')?.style.width || getComputedStyle(document.querySelector('.el-aside')).width)
  assert('侧栏折叠切换', w0 !== w1, `${w0}→${w1}`)
  await clickText('»', '.el-header button'); await new Promise(r => setTimeout(r, 400))
} catch (e) { assert('侧栏折叠', false, String(e).slice(0, 100)) }

// ---- ⌘K 真键盘（ARIA combobox）----
try {
  await p.keyboard.down('Control'); await p.keyboard.press('k'); await p.keyboard.up('Control')
  await new Promise(r => setTimeout(r, 400))
  const cmdk = await count('div[role=combobox]')
  assert('⌘K 弹窗(combobox)', cmdk > 0, `combobox=${cmdk}`)
  await p.keyboard.type('策略')
  await new Promise(r => setTimeout(r, 400))
  const opts = await count('div[role=option]')
  assert('⌘K 结果 listbox', opts > 0, `option=${opts}`)
  await p.keyboard.press('Escape'); await new Promise(r => setTimeout(r, 300))
} catch (e) { assert('⌘K', false, String(e).slice(0, 100)) }

// ---- 逐页导航零 pageerror + 组件渲染 ----
const pages = [
  { path: '/', name: '首页 Dashboard' },
  { path: '/strategy', name: '策略 Strategy' },
  { path: '/backtest', name: '回测 Backtest' },
  { path: '/trading', name: '交易台 Trading' },
  { path: '/live-task', name: '实盘任务 LiveTask' },
  { path: '/factors', name: '因子 Factors' },
  { path: '/pool', name: '标的池 Pool' },
  { path: '/screener', name: '选股器 Screener' },
  { path: '/risk', name: '风控 Risk' },
  { path: '/reconcile', name: '对账 Reconcile' },
  { path: '/integrations', name: '集成 Integrations' },
  { path: '/dataops', name: '数据 DataOps' },
  { path: '/observe', name: '健康与日志 Observe' },
]
for (const pg of pages) {
  const before = errors.length
  await nav(pg.path)
  const body = await p.evaluate(() => document.body.innerText.length)
  const newErrs = errors.length - before
  assert(`${pg.name} 渲染`, body > 50 && newErrs === 0, `文本${body}字 新pageerror=${newErrs}`)
}

// ---- 首页 KpiCard ----
try {
  await nav('/')
  const kpi = await count('.kpi-cell')
  assert('首页 KpiCard(≥4)', kpi >= 4, `kpi-cell=${kpi}`)
  assert('首页总资产卡', await hasText('总资产'))
} catch (e) { assert('首页 KpiCard', false, String(e).slice(0, 100)) }

// ---- 策略 CRUD：新建 ID 可编辑 / 编辑 ID 锁定 ----
try {
  await nav('/strategy')
  await clickText('新建策略')
  await new Promise(r => setTimeout(r, 500))
  const dlgNew = await count('.el-dialog') > 0
  assert('新建弹窗出现', dlgNew)
  const idNewDisabled = await p.evaluate(() => [...document.querySelectorAll('.el-dialog')].pop()?.querySelector('input')?.disabled)
  assert('新建态 ID 可编辑', dlgNew && idNewDisabled === false, `disabled=${idNewDisabled}`)
  await p.keyboard.press('Escape'); await new Promise(r => setTimeout(r, 300))
  const rowCount = await count('.el-table__row')
  if (rowCount > 0) {
    await clickText('编辑')
    await new Promise(r => setTimeout(r, 500))
    const idEditDisabled = await p.evaluate(() => [...document.querySelectorAll('.el-dialog')].pop()?.querySelector('input')?.disabled)
    assert('编辑态 ID 锁定', idEditDisabled === true, `disabled=${idEditDisabled}`)
    await p.keyboard.press('Escape')
  } else {
    skip('编辑态 ID 锁定', 'prod 无策略行')
  }
} catch (e) { assert('策略 CRUD', false, String(e).slice(0, 100)) }

// ---- StatusTag（回测/实盘任务状态列）----
try {
  await nav('/backtest')
  const st = await count('span.st')
  assert('StatusTag 渲染', st > 0 || (await count('.el-table__row')) === 0, `span.st=${st}`)
  await nav('/live-task')
  const st2 = await count('span.st')
  assert('LiveTask StatusTag', st2 > 0 || (await count('.el-table__row')) === 0, `span.st=${st2}`)
} catch (e) { assert('StatusTag', false, String(e).slice(0, 100)) }

// ---- TabsShell（容器页 .el-tabs）----
try {
  await nav('/screener')
  assert('TabsShell(el-tabs) Screener', (await count('.el-tabs')) > 0)
  await nav('/dataops')
  assert('TabsShell(el-tabs) DataOps', (await count('.el-tabs')) > 0)
} catch (e) { assert('TabsShell', false, String(e).slice(0, 100)) }

// ---- 成绩单（有数据则断言格式，无数据 skip）----
try {
  await nav('/backtest')
  const retCell = await p.evaluate(() => {
    const ths = [...document.querySelectorAll('.el-table__header th')]
    const idx = ths.findIndex(th => th.textContent.includes('收益'))
    if (idx < 0) return null
    const td = [...document.querySelectorAll('.el-table__row td')].filter((_, i, arr) => i % arr.length === idx)
    return td.map(t => t.textContent.trim()).join('|')
  })
  if (retCell && retCell.trim() && !retCell.split('|').every(x => x === '—' || x === '')) {
    assert('回测收益列(成绩单)', /%/.test(retCell), retCell.slice(0, 60))
  } else {
    skip('回测收益列(成绩单)', 'prod 无 done 回测数据（需跑一次回测造数）')
  }
} catch (e) { assert('成绩单', false, String(e).slice(0, 100)) }

// ---- 汇总 ----
const failed = results.filter(r => !r.ok)
console.log(`\n=== 汇总: ${results.length - failed.length}/${results.length} 通过 ===`)
if (errors.length) { console.log(`--- pageerror / console.error / 请求失败 (${errors.length}) ---`); errors.slice(0, 25).forEach(e => console.log(e)) }
await b.close()
process.exit(failed.length ? 1 : 0)
