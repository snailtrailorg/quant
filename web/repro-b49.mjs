import puppeteer from 'puppeteer-core'
const BASE = 'http://localhost:5173'
const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
await p.evaluateOnNewDocument(() => { Object.defineProperty(navigator, 'language', { get: () => 'zh-CN', configurable: true }) })
await p.setViewport({ width: 1600, height: 900 })
await p.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 30000 })
await p.type('input', 'admin')
const inputs = await p.$$('input')
await inputs[1].type('admin123')
await p.click('.el-button--primary')
await p.waitForNavigation({ waitUntil: 'networkidle2', timeout: 15000 }).catch(() => {})
await new Promise(r => setTimeout(r, 1000))

const txt = async sel => p.evaluate(s => document.querySelector(s)?.textContent?.trim() ?? '(none)', sel)

// a. /observe 页签「操作日志」
await p.goto(`${BASE}/observe`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1500))
const tabsZh = await p.evaluate(() => [...document.querySelectorAll('.el-tabs__item, [role=tab]')].map(e => e.textContent.trim()).join(' | '))
console.log('[a-zh observe 页签]', tabsZh)

// b. /logs RowFilter 触发钮 title
await p.goto(`${BASE}/logs`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1500))
const rfTitle = await p.evaluate(() => {
  const btns = [...document.querySelectorAll('button [class*=el-icon], .el-tooltip__trigger')]
  const all = [...document.querySelectorAll('[title]')]
  return all.map(e => e.getAttribute('title')).filter(Boolean).join(' | ')
})
console.log('[b-logs title 集]', rfTitle)

// b2. ColumnSettings 页 /users
await p.goto(`${BASE}/users`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1500))
const usersTitles = await p.evaluate(() => [...document.querySelectorAll('[title]')].map(e => e.getAttribute('title')).filter(Boolean).join(' | '))
console.log('[b-users title 集]', usersTitles)

// c. /perm-resources?tab=nav 三行中文
await p.goto(`${BASE}/perm-resources?tab=nav`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1800))
const navRows = await p.evaluate(() => [...document.querySelectorAll('.el-table__body tr')].slice(0, 18).map(tr => {
  const tds = [...tr.querySelectorAll('td .cell')].map(td => td.textContent.trim())
  return tds.slice(0, 3).join('/')
}).join(' | '))
console.log('[c-perm nav 行]', navRows)

// d. 弹窗 form-item margin
await p.goto(`${BASE}/settings?tab=run`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1800))
await p.evaluate(() => { [...document.querySelectorAll('[title]')].find(e => (e.getAttribute('title')||'').includes('编辑'))?.click() })
await new Promise(r => setTimeout(r, 900))
const mb = await p.evaluate(() => {
  const dlg = document.querySelector('.el-dialog')
  const fi = dlg?.querySelector('.el-form-item')
  if (!dlg || !fi) return 'NO-DLG-OR-FI dlg=' + !!dlg
  return getComputedStyle(fi).marginBottom + ' (dlg open)'
})
console.log('[d-弹窗 form-item margin-bottom]', mb)

// en 环境:切 localStorage lang=en
await p.evaluate(() => { localStorage.setItem('lang', 'en'); location.reload() })
await new Promise(r => setTimeout(r, 2000))
await p.goto(`${BASE}/observe`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1500))
const tabsEn = await p.evaluate(() => [...document.querySelectorAll('.el-tabs__item, [role=tab]')].map(e => e.textContent.trim()).join(' | '))
console.log('[a-en observe 页签]', tabsEn)
await p.goto(`${BASE}/perm-resources?tab=nav`, { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 1800))
const navRowsEn = await p.evaluate(() => [...document.querySelectorAll('.el-table__body tr')].slice(0, 18).map(tr => [...tr.querySelectorAll('td .cell')].map(td => td.textContent.trim()).slice(0, 3).join('/')).join(' | '))
console.log('[c-en perm nav 行]', navRowsEn)
await b.close()
