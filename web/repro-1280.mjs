// 1280×800 体感复验（14 号 §3 验收矩阵 Windows@150% 有效视口等效）
// 重点：总览 KPI 一行五卡不换行不挤压 / 数据管理表格列弹性+tooltip 代替换行
// 凭证走环境变量 SMOKE_USER/SMOKE_PW（禁明文入文件，与 repro-menu.mjs 的差异点）
// 用法：SMOKE_USER=admin SMOKE_PW=... node repro-1280.mjs [base]   （默认线上）
import puppeteer from 'puppeteer-core'
const base = process.argv[2] || 'https://quant.snailtrail.cc'
const user = process.env.SMOKE_USER || 'admin'
const pw = process.env.SMOKE_PW
if (!pw) { console.error('缺 SMOKE_PW（凭证见记忆 server-info 指针）'); process.exit(1) }
const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome',
  headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 })
const errors = []
p.on('pageerror', e => errors.push(String(e).slice(0, 160)))

await p.goto(base + '/login', { waitUntil: 'networkidle2', timeout: 30000 })
await p.type('input', user)
const inputs = await p.$$('input')
await inputs[1].type(pw)
await p.click('button[type=button], .el-button--primary')
await new Promise(r => setTimeout(r, 3000))

// ① Dashboard KPI 行：五卡同排？卡内截断？
await p.goto(base + '/', { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 2500))
const kpi = await p.evaluate(() => {
  const row = document.querySelector('.kpi-row')
  if (!row) return { err: 'no .kpi-row' }
  const rowTop = row.getBoundingClientRect().top
  const sameRow = row.children.length === 5 &&
    [...row.children].every(c => Math.abs(c.getBoundingClientRect().top - rowTop) < 2)
  return { n: row.children.length, sameRow, flexWrap: getComputedStyle(row).flexWrap,
    cellW: [...row.children].map(c => Math.round(c.getBoundingClientRect().width)),
    cellH: [...row.children].map(c => Math.round(c.getBoundingClientRect().height)) }
})
console.log('① KPI 行@1280:', JSON.stringify(kpi))
await p.screenshot({ path: '/tmp/v1280-dashboard.png', fullPage: false })

// ② 数据管理表格：行高一致（无换行爆炸）+ 列宽自适应
await p.goto(base + '/dataops?tab=sync', { waitUntil: 'networkidle2', timeout: 30000 })
await new Promise(r => setTimeout(r, 2500))
const tbl = await p.evaluate(() => {
  const t = document.querySelector('.el-table')
  if (!t) return { err: 'no table' }
  const rows = [...t.querySelectorAll('.el-table__body tr')].slice(0, 6)
  const hs = rows.map(r => Math.round(r.getBoundingClientRect().height))
  const firstCol = t.querySelector('thead th')?.getBoundingClientRect().width
  return { rows: rows.length, rowHeights: hs, firstNameColW: Math.round(firstCol || 0),
    bodyOverflowX: t.scrollWidth > t.clientWidth + 2 }
})
console.log('② DataManage 表@1280:', JSON.stringify(tbl))
await p.screenshot({ path: '/tmp/v1280-datamanage.png', fullPage: false })

console.log('pageerror:', errors.length ? errors : '无')
await b.close()
