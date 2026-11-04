import { spawn } from 'child_process'
import { setTimeout as sleep } from 'timers/promises'
import { writeFileSync } from 'fs'

// 批168：字号基线固化——CDP 无头登录后扫五页全部可见元素的 computed 字号，
// 产出六档分布+异常清单（非六档 px 即异常，白名单豁免如 icon 字形）。
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5173'
const chrome = spawn('/usr/bin/google-chrome', ['--headless=new','--disable-gpu','--no-sandbox','--remote-debugging-port=9444','--user-data-dir=/tmp/cdp3','--window-size=1920,1080','about:blank'], { stdio: 'ignore' })
await sleep(2200)
const targets = await (await fetch('http://127.0.0.1:9444/json')).json()
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl)
await new Promise(r => ws.onopen = r)
let id = 0; const pend = new Map()
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) } }
const send = (method, params = {}) => new Promise(res => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value
await send('Page.enable')
await send('Page.navigate', { url: BASE + '/login' })
await sleep(3500)
await ev(`(() => {
  const us = document.querySelector('input[type=text], input:not([type=password])')
  const ps = document.querySelector('input[type=password]')
  const set = (el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; s.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })) }
  set(us, '${process.env.D_USER || 'admin'}'); set(ps, '${process.env.D_PASS || 'admin123'}'); return 1
})()`)
await sleep(400)
await ev(`(() => { localStorage.setItem('lang', 'zh'); const bs = [...document.querySelectorAll('button')]; const b = bs.find(x => /登|sign/i.test(x.textContent || '')) || bs.filter(x => (x.textContent || '').trim()).at(-1); b?.click(); return b ? 'ok:' + b.textContent.trim() : 'no-btn' })()`)
await sleep(4200)
console.log('after-login url:', await ev('location.href'))

const PAGES = [['users', '用户管理'], ['screener', '筛选器'], ['dataops/sync', '数据同步'], ['observe', '系统监控'], ['settings', '系统设置']]
const rows = ['page\tel\tfont(px)\tweight\ttext']
for (const [path] of PAGES) {
  await send('Page.navigate', { url: BASE + '/' + path })
  await sleep(6500)
  console.log('page', path, 'ready=', await ev(`!!document.querySelector('.el-tabs__item, .el-card__header')`))
  const r = await ev(`(() => {
    try {
      const out = []
      for (const el of document.querySelectorAll('.el-tabs__item, .el-card__header span, .el-dialog__title, .el-collapse-item__header, .el-alert__title, .el-checkbox__label, .el-radio__label, .el-link, .el-pagination, .el-button, .el-form-item__label, .el-table .cell')) {
        const cs = getComputedStyle(el)
        const t = (el.textContent || '').trim().slice(0, 10)
        if (!t) continue
        out.push([cs.fontSize, cs.fontWeight, t].join('|'))
      }
      return out.join(String.fromCharCode(10))
    } catch (e) { return 'EVAL-ERR: ' + e.message }
  })()`)
  console.log('r type:', typeof r, 'len:', (r || '').length)
  for (const line of String(r || '').split('\n').filter(l => l && !l.startsWith('EVAL-ERR'))) rows.push(path + '\t' + line.replaceAll('|', '\t'))
}
writeFileSync('../docs/design/D24-字号基线.tsv', rows.join('\n') + '\n')
console.log('baseline rows:', rows.length - 1)
chrome.kill()
process.exit(0)
