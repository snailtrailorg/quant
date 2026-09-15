// web/scripts/check-locales.mjs —— 批27-15：locales 重复键构建期门
// vue-i18n 对象字面量重复键后值静默胜（tabs.mail / nav.settings / backtest.commission 三案在录）。
// 用 acorn 按 ObjectExpression 作用域精确判定——正则必误报（同名键跨命名空间合法出现，如 settings 6 处）。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const acorn = require('acorn')   // vite 依赖树已带（rollup 解析器），不新增依赖

const file = join(dirname(fileURLToPath(import.meta.url)), '../src/locales/index.js')
const src = readFileSync(file, 'utf8')
const ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'module' })
const dups = []
const lineOf = (pos) => src.slice(0, pos).split('\n').length

function walk(node) {
  if (!node || typeof node.type !== 'string') return
  if (node.type === 'ObjectExpression') {
    const seen = new Map()
    for (const p of node.properties) {
      if (p.type === 'Property' && !p.computed && p.key.type === 'Identifier') {
        const k = p.key.name
        if (seen.has(k)) dups.push(`  ${k}:${lineOf(p.start)}（首见 :${seen.get(k)}——后值静默胜）`)
        else seen.set(k, lineOf(p.start))
      }
    }
  }
  for (const k in node) {
    const v = node[k]
    if (Array.isArray(v)) v.forEach(walk)
    else if (v && typeof v.type === 'string') walk(v)
  }
}
walk(ast)
if (dups.length) {
  console.error('✗ locales 重复键（vue-i18n 后值静默覆盖前值）:')
  dups.forEach((d) => console.error(d))
  process.exit(1)
}
console.log('✓ locales 无重复键')
