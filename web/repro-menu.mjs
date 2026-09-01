import puppeteer from 'puppeteer-core'
const b = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome',
  headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const p = await b.newPage()
const logs = []
p.on('console', m => logs.push(`[${m.type()}] ${m.text().slice(0, 160)}`))
p.on('pageerror', e => logs.push(`[PAGEERROR] ${String(e).slice(0, 200)}`))
await p.goto('http://127.0.0.1:4173/login', { waitUntil: 'networkidle2', timeout: 30000 })
await p.type('input', 'admin')
const inputs = await p.$$('input')
await inputs[1].type('admin123')
await p.click('button[type=button], .el-button--primary')
// 时序探针:每秒读一次菜单组数,看 ops 是否迟到
for (let i = 1; i <= 8; i++) {
  await new Promise(r => setTimeout(r, 1000))
  const n = await p.evaluate(() => document.querySelectorAll('.el-menu .el-sub-menu__title').length)
  console.log(`t=${i}s 菜单组数(应5):`, n)
}
const menu = await p.evaluate(() => {
  const items = [...document.querySelectorAll('.el-menu .el-sub-menu__title, .el-menu > .el-menu-item')]
  return items.map(e => e.textContent.trim()).filter(Boolean)
})
console.log('菜单组:', JSON.stringify(menu))
const permsProbe = await p.evaluate(async () => {
  const r = await fetch('/api/auth/me', { headers: { Authorization: 'Bearer ' + localStorage.getItem('token') } })
  const d = await r.json()
  return { role: d.role, n: d.permissions?.length }
})
console.log('浏览器内 me:', JSON.stringify(permsProbe))
console.log('--- Console ---'); logs.slice(0, 12).forEach(l => console.log(l))
await b.close()
