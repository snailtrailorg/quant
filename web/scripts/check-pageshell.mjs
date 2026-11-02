// D24 8 页面结构守门（批163）。R1=header 槽裸表格红线（字体连坐——块提取非贪婪，嵌套 header 下
// 会截断但两类真实违规均可抓，误报场景本属非法写法——已知局限，精确化需 SFC 解析器不值）。
// R2=shadow=never 基线计数（豁免：卡组内嵌小卡 metric-card/kpi 类的 never 合法——D24 8.3.2；
// 基线含合法豁免项，消化只动工作区直属卡，终点非零）。
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const baselineFile = 'scripts/.pageshell-baseline'
function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (f.endsWith('.vue')) out.push(p)
  }
  return out
}
const files = walk('src')
const errs = []
let never = 0
for (const f of files) {
  const s = readFileSync(f, 'utf8')
  const hm = [...s.matchAll(/<template[^>]*#header[^>]*>([\s\S]*?)<\/template>/g)]
  for (const m of hm) {
    const block = m[1]
    if (/<TabsShell/.test(block))
      errs.push('R1 ' + f + ': header-with-TabsShell (shell mode retired, D24 s8 v2)')
    if (/<(TableShell|el-table)[\s>]/.test(block) && !/<el-card|<ContentCard/.test(block))
      errs.push('R1 ' + f + ': header naked-table (D24 s8 v2)')
  }
  never += (s.match(/shadow="never"/g) || []).length
}
let base = NaN
try { base = parseInt(readFileSync(baselineFile, 'utf8').match(/NEVER=(\d+)/)?.[1] ?? '', 10) } catch {}
if (process.argv.includes('--update') || Number.isNaN(base)) {
  writeFileSync(baselineFile, 'NEVER=' + never + '\n')
  console.log('pageshell baseline: NEVER=' + never)
} else if (never > base) {
  errs.push('R2 shadow=never ' + never + ' > baseline ' + base + ' (D24 8.3.2; --update after cleanup)')
}
if (errs.length) { console.error('pageshell FAIL:\n  ' + errs.join('\n  ')); process.exit(1) }
console.log('pageshell OK (R1=0, never=' + never + ')')
