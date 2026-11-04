import { readFileSync, writeFileSync } from 'fs'

// 批167：zh-tw 审核稿合入 locales——TSV(键<TAB>简<TAB>繁) → 嵌套对象 → 插入 en 域后
const tsv = readFileSync('../docs/翻译/zh-tw-词典工作稿-审核.tsv', 'utf8')
const lines = tsv.split('\n').filter(l => l.trim() && !l.startsWith('#') && !l.startsWith('>'))

// 构嵌套对象
const setDeep = (obj, path, val) => {
  const parts = path.split('.')
  let cur = obj
  for (let i = 0; i < parts.length - 1; i++) {
    if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {}
    cur = cur[parts[i]]
  }
  cur[parts[parts.length - 1] = parts[parts.length - 1]] = val
  cur[parts[parts.length - 1]] = val
}
const tw = {}
let n = 0
for (const line of lines) {
  const c = line.split('\t')
  if (c.length < 3) { console.error('坏行:', line.slice(0, 50)); continue }
  setDeep(tw, c[0].trim(), c[2])
  n++
}
console.log('词条数:', n)

// 序列化（键含连字符如 zh-tw 层不存在于此对象；内层键可能含 - ? 不会——键都是标识符/下划线）
const ser = (obj, indent) => {
  const pad = ' '.repeat(indent)
  const rows = []
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === 'object') rows.push(pad + k + ': {', ser(v, indent + 2), pad + '},')
    else rows.push(pad + JSON.stringify(k) + ': ' + JSON.stringify(v) + ',')
  }
  return rows.join('\n')
}
const block = '    zh-tw: {\n' + ser(tw, 6) + '\n    },\n'

const loc = readFileSync('src/locales/index.js', 'utf8')
if (loc.includes('zh-tw: {')) { console.error('zh-tw 域已存在——先移除再合入'); process.exit(1) }
// 插入点：顶层 en 域结束后的 }，后。定位：文件里 '    en: {' 的匹配闭合——用「顶层 },\n}」尾结构前插（en 是最后域）
const tailAnchor = '  },\n}'
const idx = loc.lastIndexOf(tailAnchor)
if (idx < 0) { console.error('尾部锚未命中'); process.exit(1) }
const out = loc.slice(0, idx) + '  },\n' + block + '}'
writeFileSync('src/locales/index.js', out)
console.log('zh-tw 域已合入')
