// web/scripts/check-missing-keys.mjs —— 僵尸守门②：i18n **缺失键**检测（批 171i / tabs.run 事故治本）
// 方向与 check-orphans 相反：孤儿检测查「词条无消费」（词条删除后即不可见——守门绿≠安全）；
// 本脚本查「**静态引用存在 ⇒ 词条必须存在**」——批 165 误删 tabs.run（消费方 Settings.vue
// i18nKey 动态传参形态）正是「删除后孤儿检测方向性失明」的实证（2026-11-07 事故）。
// 检测面：①t('a.b')/te()/\$t() 字面调用 ②i18nKey: 'a.b' 数据驱动（TabsShell 等）③模板绑定
// :label="t('a.b')"。动态键（`ns.${}` 模板/拼接）跳过——只钉静态可解析引用。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const acorn = require('acorn')

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

// ---- ① 叶子键提取（zh 树为真源——与 check-orphans 同法）----
const src = readFileSync(join(webRoot, 'src/locales/index.js'), 'utf-8')
const ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'module' })
const root = ast.body[0].declaration
const zhObj = root.properties.find(p => p.key.name === 'zh').value
const leaves = new Set()
;(function walk(node, prefix) {
  for (const p of node.properties) {
    if (p.type !== 'Property') continue
    const key = p.key.name ?? p.key.value
    const path = prefix ? `${prefix}.${key}` : key
    if (p.value.type === 'ObjectExpression') walk(p.value, path)
    else leaves.add(path)
  }
})(zhObj, '')

// ---- ② 消费面收集（vue/js 全 src）----
let consumers = []
;(function collect(dir) {
  for (const e of readdirSync(dir)) {
    const f = join(dir, e)
    if (statSync(f).isDirectory()) { if (e !== 'locales') collect(f) }
    else if (/\.(vue|js)$/.test(e)) consumers.push(readFileSync(f, 'utf-8'))
  }
})(join(webRoot, 'src'))
const all = consumers.join('\n')

// ---- ③ 静态引用提取 ----
const refs = new Set()
// t('a.b') / te('a.b') / $t('a.b')（含模板绑定内；te = t 前带 e 的两字母名）。
// 后跟 `+`（字符串拼接动态键）⇒ 跳过——只收完整静态键（终止于 [,)]）。
for (const m of all.matchAll(/\b[a-z]{1,2}\(\s*'([a-zA-Z0-9_.]+)'\s*[,)]/g)) refs.add(m[1])
for (const m of all.matchAll(/\$t\(\s*'([a-zA-Z0-9_.]+)'\s*[,)]/g)) refs.add(m[1])
for (const m of all.matchAll(/\b[a-z]{1,2}\(\s*"([a-zA-Z0-9_.]+)"\s*[,)]/g)) refs.add(m[1])
// i18nKey: 'a.b'（数据驱动——批 165 盲区形态；同样跳过 + 拼接）
for (const m of all.matchAll(/i18nKey:\s*'([a-zA-Z0-9_.]+)'\s*[,}\n]/g)) refs.add(m[1])

// ---- ④ 缺失判定：静态引用 ∉ 叶子集 ⇒ 红 ----
// 引用可能指向**中间节点**（t('ns') 传对象）——放过：只对「点分≥2 且父路径也不在叶集」判缺失
const missing = [...refs].filter(r => {
  if (leaves.has(r)) return false
  if (!r.includes('.')) return false          // 单段=命名空间引用，保守放行
  // 父路径是叶子（对象值引用）⇒ 放行
  const parts = r.split('.')
  for (let i = 1; i < parts.length; i++) {
    if (leaves.has(parts.slice(0, i).join('.'))) return false
  }
  return true
})

if (missing.length) {
  console.log(`✗ 缺失词条 ${missing.length} 处（静态引用在但词条不在 zh 树——补回或改引用）：`)
  for (const m of missing) console.log('  ' + m)
  process.exit(1)
}
console.log(`✓ 缺失词条 0 处（静态引用 ${refs.size} 个全覆盖；${leaves.size} 叶子键）`)
