// web/scripts/check-orphans.mjs —— 僵尸守门①：孤儿 i18n 词条扫描（批 127·Z3）
// 逻辑：acorn 解析 locales zh 树提叶子键路径 × 全 src 消费面比对；
// 动态键（t(`ns.${var}`)）ns 级保守跳过。挂 prebuild（check-tokens → locales → coldiff → orphans）。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const acorn = require('acorn')

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(webRoot, 'src/locales/index.js'), 'utf-8')
const ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'module' })

const root = ast.body[0].declaration
const zhObj = root.properties.find(p => p.key.name === 'zh').value
const leaves = []
;(function walk(node, prefix) {
  for (const p of node.properties) {
    if (p.type !== 'Property') continue
    const key = p.key.name ?? p.key.value
    const path = prefix ? `${prefix}.${key}` : key
    if (p.value.type === 'ObjectExpression') walk(p.value, path)
    else leaves.push(path)
  }
})(zhObj, '')

let consumers = ''
;(function collect(dir) {
  for (const e of readdirSync(dir)) {
    const f = join(dir, e)
    if (statSync(f).isDirectory()) { if (e !== 'locales') collect(f) }
    else if (/\.(vue|js)$/.test(e)) consumers += readFileSync(f, 'utf-8')
  }
})(join(webRoot, 'src'))

const dynamicNs = new Set()
for (const m of consumers.matchAll(/[te]\(`(\w+)\.\$\{/g)) dynamicNs.add(m[1])
// 模板动态键保护（批127 实证误报族）：`ns.a.${`/`ns.a.b.${` 等模板串——其父路径全体保守跳过
// 例：t(`err.${code}`) 保护 err.*；`interfaces.field.${k}` 保护 interfaces.field.*；
//     `perm.key_${k}`（非点拼接）保护 perm.key_ 前缀；`alerts.dispatch.${s}` 同理
const dynamicPrefixes = []   // [{prefix}]——点分模板
const dynamicRawPrefix = [] // 原文含 ${ 的键前缀（含下划线拼接）
for (const m of consumers.matchAll(/['"`]([a-zA-Z0-9_.]+)\.\$\{[^`'"]*['"`]/g)) dynamicPrefixes.push(m[1])
for (const m of consumers.matchAll(/['"`]([a-zA-Z0-9_.]+)_\$\{/g)) dynamicRawPrefix.push(m[1])
// 字符串 + 拼接形态（apiErr 实证：'err.' + e.code——批127 Z3 误报主源）
for (const m of consumers.matchAll(/['"`]([a-zA-Z0-9_.]+)\.\s*'\s*\+/g)) dynamicPrefixes.push(m[1])
for (const m of consumers.matchAll(/['"`]([a-zA-Z0-9_.]+)_\s*'\s*\+/g)) dynamicRawPrefix.push(m[1])
// 反引号含引号内静态头（`err.${` 已覆盖）；te/t 之外的 $t('ns.'+ 也收（宽松收集即可）

const orphans = leaves.filter(p => {
  if (consumers.includes(p)) return false
  const ns = p.split('.')[0]
  if (dynamicNs.has(ns)) return false
  // 点分模板：p 的任一父路径是动态模板前缀 ⇒ 跳过
  for (const pre of dynamicPrefixes) if (p.startsWith(pre + '.')) return false
  // 下划线拼接模板：p 以动态前缀开头（perm.key_${k} → perm.key_read 等被动态产出）
  for (const pre of dynamicRawPrefix) if (p.startsWith(pre + '_')) return false
  return true
})

if (orphans.length) {
  console.log(`✗ 孤儿词条 ${orphans.length} 处（无 t() 消费——删或用）：`)
  for (const o of orphans) console.log('  ' + o)
  process.exit(1)
}
console.log(`✓ 孤儿词条 0 处（${leaves.length} 叶子键全有消费）`)
