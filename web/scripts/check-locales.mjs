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

// 批 71 守门（第四门）：词条占位符 zh/en 一致——同一键 {x} 占位符名字集合（去重）相等。
// 单侧缺占位=该语言渲染残缺（vue-i18n 原样显示 {x}）；集合语义非列表（同名多次出现合法）。
// JSON 示例类词条（如 IFACE_PARAMS_INVALID）花括号带引号不匹配 /\{(\w+)\}/，无误报（已核）。
{
  const root2 = ast.body.find(s => s.type === 'ExportDefaultDeclaration')?.declaration
  const zh2 = root2?.properties?.find(p => p.key.name === 'zh')?.value
  const en2 = root2?.properties?.find(p => p.key.name === 'en')?.value
  const collectMap = (node, prefix, out) => {
    if (!node || node.type !== 'ObjectExpression') return
    for (const p of node.properties) {
      if (p.type !== 'Property' || p.computed || p.key.type !== 'Identifier') continue
      const path = prefix ? `${prefix}.${p.key.name}` : p.key.name
      if (p.value.type === 'ObjectExpression') collectMap(p.value, path, out)
      else if (p.value.type === 'Literal' && typeof p.value.value === 'string') out.set(path, p.value.value)
    }
  }
  const ph = (s) => new Set([...String(s).matchAll(/\{(\w+)\}/g)].map(m => m[1]))
  const zhMap = new Map(), enMap = new Map()
  collectMap(zh2, '', zhMap); collectMap(en2, '', enMap)
  const bad = []
  for (const [k, v] of zhMap) {
    const ev = enMap.get(k)
    if (ev === undefined) continue   // 单侧缺键第三门已管
    const a = ph(v), b = ph(ev)
    if (a.size !== b.size || [...a].some(x => !b.has(x)))
      bad.push(`  ${k}: zh {${[...a].join(',')}} vs en {${[...b].join(',')}}`)
  }
  if (bad.length) {
    console.error(`✗ 词条占位符 zh/en 不一致 ${bad.length} 处:`)
    bad.forEach(x => console.error(x))
    process.exit(1)
  }
  console.log('✓ 词条占位符 zh/en 一致（集合语义）')
}

// 批 132 第 5 门：vue-i18n 消息保留符校验（{ } @ |）——未转义的保留符要么 build 硬错（{ } @），
// 要么**静默腐化**（| 是复数分隔符：build 绿但只显示最后一支，实测 groupAvg 前三段全丢）。
// 用 acorn AST 取字符串字面量（正则跨行必误报）；合法占位符 {name}/{n} 与转义形态 {'x'} 放行。
{
  const bads = []
  const walk = (node, path) => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'Literal' && typeof node.value === 'string') {
      let s = node.value
      if (!/[{}@|]/.test(s)) return
      s = s.replace(/\{'(.)'\}/g, '')                     // 已转义形态 {'x'} 全部摘除
      s = s.replace(/\{[A-Za-z_][A-Za-z0-9_]*\}/g, '')     // 命名占位符
      s = s.replace(/\{\d+\}/g, '')                       // 索引占位符
      if (/[{}@|]/.test(s)) bads.push('  ' + path + ': ' + node.value.slice(0, 70))
      return
    }
    for (const k of Object.keys(node)) {
      const v = node[k]
      if (Array.isArray(v)) v.forEach((x, idx) => walk(x, path))
      else if (v && typeof v === 'object') walk(v, path)
    }
  }
  walk(ast, 'locales')
  if (bads.length) {
    console.error('✗ 词条含未转义的 vue-i18n 保留符（{ } @ |）' + bads.length + ' 处——用 {\'x\'} 转义:')
    bads.slice(0, 10).forEach(x => console.error(x))
    process.exit(1)
  }
  console.log('✓ 词条无未转义保留符（{ } @ |）')
}
