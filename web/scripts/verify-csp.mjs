// verify-csp.mjs —— 批 132：CSP 严格模式下 dist 真实渲染验证（10-12 事故同款复现法）。
// 用法：cd web && node scripts/verify-csp.mjs
// 无后端档：带生产同款 CSP 的静态服务（history 回退）→ /login × zh/en
// 判据：securitypolicyviolation=0 && pageerror=0 && 关键选择器存在
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import puppeteer from 'puppeteer-core'

const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../dist')
const PORT = Number(process.env.CSP_PORT || 5199)
// 生产同款 CSP（quant.conf 的 script-src 'self'，无 unsafe-eval = 本批目标态）
const CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws: wss:; " +
  "frame-ancestors 'self'; base-uri 'self'"

const MIME = { '.js': 'text/javascript', '.css': 'text/css',
  '.html': 'text/html; charset=utf-8', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split('?')[0])
  let file = path.join(DIST, urlPath)
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html')
  const ext = path.extname(file)
  res.setHeader('Content-Type', MIME[ext] || 'application/octet-stream')
  if (ext === '.html') res.setHeader('Content-Security-Policy', CSP)
  fs.createReadStream(file).pipe(res)
})
await new Promise(r => server.listen(PORT, r))
console.log('[serve] http://127.0.0.1:' + PORT + ' CSP=strict')

const browser = await puppeteer.launch({
  executablePath: '/usr/bin/google-chrome', headless: 'new',
  args: ['--no-sandbox', '--disable-gpu'],
})

let failed = false
for (const lang of ['zh-CN', 'en-US']) {
  const page = await browser.newPage()
  const errors = []
  await page.evaluateOnNewDocument((l) => {
    Object.defineProperty(navigator, 'language', { get: () => l, configurable: true })
    Object.defineProperty(navigator, 'languages', { get: () => [l], configurable: true })
    window.__cspViolations = []
    document.addEventListener('securitypolicyviolation', (e) => {
      window.__cspViolations.push(e.violatedDirective + ' ' + (e.blockedURI || ''))
    })
  }, lang)
  page.on('pageerror', e => errors.push(String(e).slice(0, 200)))
  await page.goto('http://127.0.0.1:' + PORT + '/login', { waitUntil: 'networkidle2' })
  await new Promise(r => setTimeout(r, 800))
  const violations = await page.evaluate(() => window.__cspViolations || [])
  const dom = await page.evaluate(() => ({
    len: document.body.innerHTML.length,
    hasPwd: !!document.querySelector('input[type=password]'),
    appKids: document.querySelector('#app') ? document.querySelector('#app').children.length : 0,
    text: (document.body.innerText || '').slice(0, 120),
  }))
  const ok = violations.length === 0 && errors.length === 0 && dom.hasPwd && dom.appKids > 0
  console.log('[' + lang + '] ' + (ok ? 'PASS' : 'FAIL') +
    ' violations=' + violations.length + ' pageerrors=' + errors.length +
    ' dom=' + dom.len + ' pwd=' + dom.hasPwd + ' appKids=' + dom.appKids)
  if (violations.length) console.log('   violation:', violations.slice(0, 5))
  if (errors.length) console.log('   pageerror:', errors.slice(0, 3))
  if (dom.text) console.log('   text:', dom.text.replace(/\s+/g, ' ').slice(0, 80))
  if (!ok) failed = true
  await page.close()
}
await browser.close()
server.close()
console.log(failed ? 'RESULT: FAIL' : 'RESULT: PASS')
process.exit(failed ? 1 : 0)
