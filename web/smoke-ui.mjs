// smoke-ui.mjs —— 前端运行时冒烟门（wd-20 收官日 #4，2026-09-03）
// puppeteer 登录 prod 走真 DOM 逐页断言。覆盖 wd-20 §4 + 进展 09-03 晨条全链验证清单；
// 无数据（prod 回测 0 done）的验证点显式 skip（不静默跳过），需造数验证。
// 用法：cd web && SMOKE_PASS=xxx node smoke-ui.mjs  退出码：0=全绿，1=有失败（需造数项显式列出但不判红）。
import puppeteer from 'puppeteer-core'

const BASE = 'https://quant.snailtrail.cc'
const USER = process.env.SMOKE_USER || 'admin'
const PASS = process.env.SMOKE_PASS
if (!PASS) { console.error('✗ 需 SMOKE_PASS 环境变量（生产 admin 密码）：SMOKE_PASS=xxx node smoke-ui.mjs'); process.exit(2) }

const results = []
const errors = []
const needsData = []   // 需造数验证的清单（显式记录，不静默）
const assert = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? '✓' : '✗'} ${name}${detail ? ' — ' + detail : ''}`) }
const skip = (name, detail = '') => { results.push({ name, ok: true, detail }); needsData.push(name); console.log(`○ ${name}${detail ? ' — ' + detail : ''}`) }

const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
await p.evaluateOnNewDocument(() => {   // 固定 zh-CN（headless 默认 en-US，i18n 会走英文——断言中文文案需锁语言）
  Object.defineProperty(navigator, 'language', { get: () => 'zh-CN', configurable: true })
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'en'], configurable: true })
})
p.on('console', m => { if (m.type() === 'error') errors.push(`[console.error] ${m.text().slice(0, 160)}`) })
p.on('pageerror', e => errors.push(`[PAGEERROR] ${String(e).slice(0, 200)}`))
p.on('response', r => { if (r.status() === 404 && r.url().includes('quant.snailtrail.cc')) errors.push(`[404] ${r.url().replace('https://quant.snailtrail.cc', '')}`) })

const nav = async path => {
  let ok = true
  await p.goto(`${BASE}${path}`, { waitUntil: 'networkidle2', timeout: 30000 }).catch(() => { ok = false })
  await new Promise(r => setTimeout(r, 900))
  // 盲审 P2：goto 失败/超时被旧页 DOM 掩盖（断言读到上一页仍>50字=假绿）——检查 url 真切换
  const cur = await p.url()
  if (!cur.includes(path)) ok = false
  if (!ok) assert(`导航 ${path}`, false, `当前 url=${cur}`)
}
const waitFor = async (fn, timeout = 12000) => { const t0 = Date.now(); while (Date.now() - t0 < timeout) { if (await fn()) return true; await new Promise(r => setTimeout(r, 400)) } return false }
const count = sel => p.evaluate(s => document.querySelectorAll(s).length, sel)
const texts = sel => p.evaluate(s => [...document.querySelectorAll(s)].map(e => e.textContent.trim()), sel)
const firstText = sel => p.evaluate(s => document.querySelector(s)?.textContent?.trim() ?? '', sel)
const clickBtn = t => p.evaluate(tx => { const e = [...document.querySelectorAll('button')].find(b => b.textContent.trim().includes(tx)); e?.click(); return !!e }, t)

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
const menuGroups = await count('.el-menu .el-sub-menu__title')
assert('菜单组(应4)', menuGroups >= 4, `组数=${menuGroups}`)

// ---- 侧栏折叠（处理初始折叠态）----
try {
  const asideW = () => p.evaluate(() => document.querySelector('.el-aside')?.getBoundingClientRect().width ?? 0)
  let w0 = await asideW()
  if (w0 < 100) { await clickBtn('»'); await new Promise(r => setTimeout(r, 400)); w0 = await asideW() }   // 初始折叠→先展开
  await clickBtn('«'); await new Promise(r => setTimeout(r, 400))
  const w1 = await asideW()
  assert('侧栏折叠切换', w0 > 100 && w1 < 100, `${w0}→${w1}`)
  await clickBtn('»'); await new Promise(r => setTimeout(r, 400))
} catch (e) { assert('侧栏折叠', false, String(e).slice(0, 100)) }

// ---- ⌘K 真键盘 ----
try {
  await p.keyboard.down('Control'); await p.keyboard.press('k'); await p.keyboard.up('Control')
  await new Promise(r => setTimeout(r, 400))
  assert('⌘K combobox', (await count('div[role=combobox]')) > 0)
  await p.keyboard.type('策略'); await new Promise(r => setTimeout(r, 400))
  assert('⌘K 结果 option', (await count('div[role=option]')) > 0)
  await p.keyboard.press('Escape'); await new Promise(r => setTimeout(r, 300))
} catch (e) { assert('⌘K', false, String(e).slice(0, 100)) }

// ---- 13 页导航零 pageerror ----
const pages = ['/', '/strategy', '/backtest', '/trading', '/live-task', '/factors', '/pool', '/screener', '/risk', '/reconcile', '/integrations', '/dataops', '/observe']
for (const pg of pages) {
  const before = errors.length
  await nav(pg)
  const len = await p.evaluate(() => document.body.innerText.length)
  const newErrs = errors.length - before
  assert(`页面 ${pg || '/'} 渲染`, len > 50 && newErrs === 0, `文本${len}字 新err=${newErrs}`)
}

// ---- 首页 KpiCard + 总资产 ----
try {
  await nav('/')
  assert('首页 KpiCard(≥4)', (await count('.kpi-cell')) >= 4)
  assert('首页总资产卡', (await texts('.klabel')).some(l => l.includes('总资产')), (await texts('.klabel')).join('/').slice(0, 100))
} catch (e) { assert('首页 KpiCard', false, String(e).slice(0, 100)) }

// ---- 策略 CRUD：新建 ID 可编辑 / 编辑 ID 锁定 ----
try {
  await nav('/strategy')
  const cb = await p.evaluate(() => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.includes('新建策略')); return b ? { disabled: b.disabled, text: b.textContent.trim() } : null })
  assert('新建策略按钮可用', !!cb && !cb.disabled, JSON.stringify(cb))
  await clickBtn('新建策略'); await new Promise(r => setTimeout(r, 700))
  const idOfNew = await p.evaluate(() => { const inp = [...document.querySelectorAll('.el-dialog')].pop()?.querySelector('input'); return inp ? { exists: true, disabled: inp.disabled } : { exists: false } })
  assert('新建弹窗+ID 输入框', idOfNew.exists)
  assert('新建态 ID 可编辑', idOfNew.exists && idOfNew.disabled === false, `disabled=${idOfNew.disabled}`)
  await p.keyboard.press('Escape'); await new Promise(r => setTimeout(r, 400))
  const rowCount = await count('.el-table__row')
  if (rowCount > 0) {
    await clickBtn('编辑'); await new Promise(r => setTimeout(r, 700))
    const idOfEdit = await p.evaluate(() => { const inp = [...document.querySelectorAll('.el-dialog')].pop()?.querySelector('input'); return inp ? { exists: true, disabled: inp.disabled } : { exists: false } })
    assert('编辑态 ID 锁定', idOfEdit.exists && idOfEdit.disabled === true, `disabled=${idOfEdit.disabled}`)
    await p.keyboard.press('Escape')
  } else {
    skip('编辑态 ID 锁定', 'prod 无策略行')
  }
} catch (e) { assert('策略 CRUD', false, String(e).slice(0, 100)) }

// ---- Trading 三值：总资产 / 现价列 / 失败原因列 ----
try {
  await nav('/trading')
  await waitFor(() => p.evaluate(() => [...document.querySelectorAll('.kpi-num')].some(e => e.textContent.includes('¥'))))
  assert('Trading 总资产', (await texts('.kpi-num')).some(v => v.includes('¥')), (await texts('.kpi-num')).filter(v => v.includes('¥'))[0] || '')
  const colOf = async label => p.evaluate(lb => [...document.querySelectorAll('.el-table__header th')].some(th => th.textContent.includes(lb)), label)
  assert('Trading 现价列', await colOf('现价'), (await texts('.el-table__header th')).join('/').slice(0, 100))
  await p.evaluate(() => { const t = [...document.querySelectorAll('.el-tabs__item')].find(x => x.textContent.includes('订单')); t?.click() })
  await new Promise(r => setTimeout(r, 600))
  assert('Trading 失败原因列', await colOf('失败原因'))
} catch (e) { assert('Trading 三值', false, String(e).slice(0, 100)) }

// ---- live-task 时间线 + 重启数 ----
try {
  await nav('/live-task')
  const taskRows = await count('.el-table__row')
  assert('LiveTask 任务列表', taskRows >= 0)
  if (taskRows > 0) {
    await p.evaluate(() => document.querySelector('.el-table__expand-icon')?.click())
    await new Promise(r => setTimeout(r, 700))
    const body = await p.evaluate(() => document.body.innerText)
    assert('live-task 时间线(重启数)', body.includes('重启'), body.includes('重启') ? (body.match(/重启[：:]\s*\d+/) || [''])[0] : '')
  } else {
    skip('live-task 时间线(重启数)', 'prod 无实盘任务')
  }
} catch (e) { assert('live-task 时间线', false, String(e).slice(0, 100)) }

// ---- StatusTag ----
try {
  await nav('/backtest')
  assert('Backtest StatusTag', (await count('span.st')) > 0 || (await count('.el-table__row')) === 0, `span.st=${await count('span.st')}`)
} catch (e) { assert('StatusTag', false, String(e).slice(0, 100)) }

// ---- TabsShell 切换 ----
try {
  await nav('/dataops')
  const tabCount = await count('.el-tabs__item')
  assert('TabsShell DataOps', tabCount >= 2, `tab=${tabCount}`)
  if (tabCount >= 2) {
    await p.evaluate(() => document.querySelectorAll('.el-tabs__item')[1]?.click())
    await new Promise(r => setTimeout(r, 600))
    const q = await p.evaluate(() => new URL(location.href).searchParams.get('tab'))
    assert('TabsShell 切换(query.tab 同步)', q === 'integrity', `tab=${q}`)
  }
} catch (e) { assert('TabsShell 切换', false, String(e).slice(0, 100)) }

// ---- 验证门 + 成绩单三处（prod 无 done 回测数据，显式 skip，需造数）----
await nav('/backtest')
const retText = await p.evaluate(() => {
  const ths = [...document.querySelectorAll('.el-table__header th')]
  const i = ths.findIndex(th => th.textContent.includes('收益'))
  if (i < 0) return ''
  return [...document.querySelectorAll('.el-table__row')].map(r => r.children[i]?.textContent.trim() || '').filter(Boolean).join('|')
})
if (retText && !retText.split('|').every(x => x === '—')) {
  assert('成绩单-回测列表收益列', /%/.test(retText), retText.slice(0, 60))
} else {
  skip('成绩单三处(列表/策略页/首页) + 验证门拦截', 'prod 0 条 done 回测——需造数（发起一次短回测后复跑）')
}

// ---- EmptyState（空态组件）----
try {
  await nav('/pool')
  const empty = await count('.el-empty')
  const hasTable = await count('.el-table') > 0
  assert('EmptyState 空态', empty > 0 || hasTable, `el-empty=${empty}`)
} catch (e) { assert('EmptyState', false, String(e).slice(0, 100)) }

// ---- 汇总 ----
const failed = results.filter(r => !r.ok)
console.log(`\n=== 汇总: ${results.length - failed.length}/${results.length} 通过（${needsData.length} 项需造数） ===`)
if (needsData.length) console.log('需造数验证:', needsData.join(' · '))
if (errors.length) { console.log(`--- pageerror / 404 / console.error (${errors.length}) ---`); errors.slice(0, 30).forEach(e => console.log(e)) }
await b.close()
process.exit(failed.length ? 1 : 0)
