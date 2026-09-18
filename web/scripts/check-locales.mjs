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

// 批42 守门：行内注释吞键检测——键被 // 注释静默失效（zh 缺键→静默回落 en→中文界面出英文）
{
  const srcLines = src.split('\n')
  const swallowed = []
  for (let i = 0; i < srcLines.length; i++) {
    const ln = srcLines[i]
    const idx = ln.indexOf('//')
    if (idx < 0) continue
    const m = ln.slice(idx).match(/[a-zA-Z_][a-zA-Z0-9_]*: '/g)
    if (m) swallowed.push(`  行${i + 1} 注释后含键（已静默失效）: ${m.map(x => x.replace(/: .*/, '')).join(',')} — ${ln.slice(idx).trim().slice(0, 60)}`)
  }
  if (swallowed.length) {
    console.error(`✗ 行内注释吞键 ${swallowed.length} 处:`)
    swallowed.forEach(x => console.error(x))
    process.exit(1)
  }
  console.log('✓ 行内注释吞键 0 处')
}

// 批45 守门：en/zh 键集对称——单侧缺键=另一语言环境静默回落（zh 缺→中文界面出英文；en 缺→英文界面出中文）。
// 现状盲区实证：en 缺 systemConfig.desc.* → 英文环境配置说明回落 DB 中文。
{
  const collect = (node, prefix, out) => {
    if (!node || node.type !== 'ObjectExpression') return
    for (const p of node.properties) {
      if (p.type !== 'Property' || p.computed || p.key.type !== 'Identifier') continue
      const path = prefix ? `${prefix}.${p.key.name}` : p.key.name
      if (p.value.type === 'ObjectExpression') collect(p.value, path, out)
      else out.add(path)
    }
  }
  const root = ast.body.find(s => s.type === 'ExportDefaultDeclaration')?.declaration
  const zhNode = root?.properties?.find(p => p.key.name === 'zh')?.value
  const enNode = root?.properties?.find(p => p.key.name === 'en')?.value
  if (!zhNode || !enNode) {
    console.error('✗ 未找到顶层 zh/en 段（结构变更？）')
    process.exit(1)
  }
  const zhKeys = new Set(), enKeys = new Set()
  collect(zhNode, '', zhKeys); collect(enNode, '', enKeys)
  const zhOnly = [...zhKeys].filter(k => !enKeys.has(k))
  const enOnly = [...enKeys].filter(k => !zhKeys.has(k))
  if (zhOnly.length || enOnly.length) {
    console.error(`✗ en/zh 键集不对称（zh-only ${zhOnly.length} / en-only ${enOnly.length}）:`)
    zhOnly.forEach(k => console.error(`  仅 zh: ${k}`))
    enOnly.forEach(k => console.error(`  仅 en: ${k}`))
    process.exit(1)
  }
  console.log(`✓ en/zh 键集对称（${zhKeys.size} 键）`)
}
