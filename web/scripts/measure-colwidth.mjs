// 批52：表格列宽双语测量（离线工具——用户裁定：系统外评估只算缺省值，不挂守门）
// 输出建议列宽清单：file:line / 列 / 现值 / 建议值 / 列型。用法：node scripts/measure-colwidth.mjs
// 解析形态（快审 A-P1-1）：块级捕获 <el-table-column ...>（跨行至开标签 >）+紧随 #header slot
// （拖拽列预算=图标宽非词条）；:label="t('直键')"（拼接键全站零命中——实证）；词条 zh/en
// 取 max；余量策略（用户裁定）：固定型 +15% 美观留白 / 变长型 +40% 宁宽勿紧。
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
const require = createRequire(import.meta.url)
import { createRequire } from 'node:module'
const acorn = require('acorn')

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

// ——— 词条 zh/en 读取（复用 check-locales 的 AST 定位思路）———
const locSrc = readFileSync(join(root, 'src/locales/index.js'), 'utf8')
const locAst = acorn.parse(locSrc, { ecmaVersion: 'latest', sourceType: 'module' })
const rootObj = locAst.body.find(s => s.type === 'ExportDefaultDeclaration')?.declaration
const zhObj = rootObj?.properties?.find(p => p.key.name === 'zh')?.value
const enObj = rootObj?.properties?.find(p => p.key.name === 'en')?.value
const flat = (node, prefix, out) => {
  if (!node || node.type !== 'ObjectExpression') return
  for (const p of node.properties) {
    if (p.type !== 'Property' || p.computed || p.key.type !== 'Identifier') continue
    const path = prefix ? `${prefix}.${p.key.name}` : p.key.name
    if (p.value.type === 'ObjectExpression') flat(p.value, path, out)
    else if (p.value.type === 'Literal') out[path] = String(p.value.value)
  }
}
const ZH = {}, EN = {}
flat(zhObj, '', ZH); flat(enObj, '', EN)

const textPx = (v) => {
  const s = v === null || v === undefined ? '' : String(v)
  let w = 0
  for (const ch of s) w += ch.charCodeAt(0) > 127 ? 15 : 8
  return w
}

// ——— 列型判定+类型下限+余量（用户裁定余量策略）———
const TYPED = [
  { re: /(updated_at|created_at|_at|_time|sent_at|\bts\b|time)/i, floor: 176, kind: '时间戳(固定)', cap: 220 },
  { re: /(status|state|level|type|enabled)\b/i, floor: 0, kind: '枚举(固定)' },
  { re: /(phone|symbol|code)/i, floor: 0, kind: '编码(固定)' },
]
const VARLEN = /(name|desc|remark|path|title|label|detail|url|content|params|message|error|note|nickname|username|model|provider|host)/i
const colKind = (prop, label) => {
  for (const t of TYPED) if (t.re.test(prop || '') || t.re.test(label || '')) return t
  if (VARLEN.test(prop || '') || VARLEN.test(label || '')) return { floor: 0, kind: '变长' }
  return { floor: 0, kind: '默认' }
}
const suggest = (prop, labelZh, labelEn, cur) => {
  const header = Math.max(textPx(labelZh), textPx(labelEn))
  const t = colKind(prop, labelZh)
  let base = Math.max(header, t.floor) + 24   // +EP .cell 双侧 padding
  if (t.kind === '变长') base = Math.round(base * 1.4)          // 宁宽勿紧（用户裁定）
  else base = Math.round(base * 1.15)                            // 固定型美观留白
  if (t.cap) base = Math.min(base, t.cap)                        // 时间戳封顶（fmtTime.full 19 字符实测）
  return { val: Math.max(80, base), kind: t.kind }
}

// ——— 遍历 vue 文件,块级捕获列 ———
const allFiles = []
for (const d of ['views', 'components'])
  for (const f of readdirSync(join(root, `src/${d}`)).filter(f => f.endsWith('.vue')))
    allFiles.push(join(root, `src/${d}`, f))

const rows = []
for (const fp of allFiles) {
  const src = readFileSync(fp, 'utf8')
  const lines = src.split('\n')
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].includes('<el-table-column')) continue
    // 块捕获:从本行 <el-table-column 到开标签 > 结束(可能跨行)
    let j = i, block = ''
    for (; j < Math.min(lines.length, i + 12); j++) {
      block += lines[j] + '\n'
      const close = block.indexOf('>', block.indexOf('<el-table-column'))
      if (close >= 0 && !/\/>$/.test(block.slice(0, close + 1).trimEnd()) && !block.slice(0, close).endsWith('/')) {
        // 闭角已现——若自闭合(/>)即止;否则开标签止
        if (block.slice(0, close).endsWith('/')) continue
        break
      }
      if (close >= 0) break
    }
    const mW = block.match(/min-width="(\d+)"/) || block.match(/:min-width="(\d+)"/)
    const wW = block.match(/\bwidth="(\d+)"/) || block.match(/:\bwidth="(\d+)"/)
    if (!mW && !wW) continue
    const cur = mW ? +mW[1] : +wW[1]
    const mode = mW ? 'min-width' : 'width'
    // 表头:#header slot(图标列)/:label="t('k')"/静态 label
    const after = lines.slice(i, i + 12).join('\n')
    const hasHeaderSlot = /<template #header/.test(after)
    const lm = block.match(/:label="t\('([^']+)'\)"/) || block.match(/:label="t\(`([^`]+)`\)"/)
    const sm = block.match(/\slabel="([^"]+)"/)
    const prop = (block.match(/prop="([^"]+)"/) || [])[1] || ''
    let zh, en
    if (hasHeaderSlot) continue                           // 图标列(批46/47/50 拖拽列):预算=图标宽≈40,现值 46 合理——跳过(快审 A-P1-1 假阳性防线)
    else if (lm) { zh = ZH[lm[1]]; en = EN[lm[1]] }
    else if (sm) { zh = sm[1]; en = sm[1] }
    if (!hasHeaderSlot && zh === undefined && en === undefined) {
      rows.push({ fp: fp.replace(root + '/', ''), line: i + 1, label: lm?.[1] || sm?.[1] || prop || '?', cur, mode, sug: null, kind: '词条缺失(跳过)' })
      continue
    }
    const s = suggest(prop, zh, en, cur)
    rows.push({ fp: fp.replace(root + '/', ''), line: i + 1, label: lm?.[1] || sm?.[1] || prop || '(图标列)', cur, mode, sug: s.val, kind: s.kind })
  }
}

// ——— 输出 ———
const need = rows.filter(r => r.sug && (r.mode === 'min-width' ? r.sug > r.cur + 8 : r.sug > r.cur + 8))
const shrink = rows.filter(r => r.sug && r.sug < r.cur - 40)   // 富余过大(只提示不强推——变长列本批不收)
console.log(`共列(带静态宽):${rows.length}  建议加宽:${need.length}  富余较大:${shrink.length}  词条缺失:${rows.filter(r => !r.sug).length}`)
console.log('\n=== 建议加宽(现值不足双语预算+余量) ===')
for (const r of need.sort((a, b) => (b.sug - b.cur) - (a.sug - a.cur)))
  console.log(`${r.fp}:${r.line}  [${r.kind}] ${r.label}  ${r.mode} ${r.cur} → ${r.sug}`)
console.log('\n=== 富余较大(仅参考——变长列宁宽勿紧不收) ===')
for (const r of shrink.slice(0, 20))
  console.log(`${r.fp}:${r.line}  [${r.kind}] ${r.label}  ${r.mode} ${r.cur} (预算 ${r.sug})`)
