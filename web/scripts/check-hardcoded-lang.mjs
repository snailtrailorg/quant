import { readFileSync, readdirSync, writeFileSync } from 'fs'
import { join, basename } from 'path'

// 批167：硬编码语言扫描——t() 调用外的 UI 可见中文/英文句子字面量（模板文本节点+JS 字符串）
// 白名单：注释行 / console.log / 正则 / 键名标识符 / 纯标点数字 / 域名 URL / storage key
const SRC = join(process.cwd(), 'src')
const files = []
const walk = d => { for (const f of readdirSync(d, { withFileTypes: true })) { const p = join(d, f.name); if (f.isDirectory()) walk(p); else if (/\.vue$|\.js$/.test(f.name)) files.push(p) } }
walk(SRC)

const hits = []
for (const f of files) {
  const lines = readFileSync(f, 'utf8').split('\n')
  let inBlock = false, inHtml = false
  lines.forEach((rawLine, i) => {
    let line = rawLine
    // 块注释/HTML 注释跨行状态机
    if (inBlock || inHtml) {
      if (inBlock && line.includes('*/')) { line = line.slice(line.indexOf('*/') + 2); inBlock = false }
      else if (inHtml && line.includes('-->')) { line = line.slice(line.indexOf('-->') + 3); inHtml = false }
      else return
    }
    if (line.includes('/*')) { line = line.slice(0, line.indexOf('/*')); if (!line.includes('*/')) inBlock = true }
    if (line.includes('<!--')) { line = line.slice(0, line.indexOf('<!--')); if (!line.includes('-->')) inHtml = true }
    if (/^\s*\/\//.test(line)) return                    // 整行注释
    line = line.replace(/\/\/[^"\n]*$/, '')            // 行内尾注（保守：到引号停）
    if (!line.trim()) return
    if (/console\.|throw new|logger\.|test\(|expect\(/.test(line)) return
    // 模板文本节点里的裸中文（不在 {{ }} / 属性里的中文句子——含 4+ 连续汉字即报）
    for (const m of line.matchAll(/([\u4e00-\u9fff]{4,})/g)) {
      const s = m[1]
      if (/^[\u4e00-\u9fff]+$/.test(s) && !/t\(/.test(line)) hits.push(`${basename(f)}:${i + 1} 模板中文「${s}」`)
      else if (/t\(/.test(line) && !/t\('[^']*'/.test(s) && /['"`]${s}['"`]/.test(line) && !/i18nKey|label_key/.test(line)) hits.push(`${basename(f)}:${i + 1} 可疑「${s}」`)
    }
  })
}
if (hits.length) {
  console.log(`⚠ 疑似硬编码语言串 ${hits.length} 处（含注释残渣/数据 doc 类——报告落 docs/翻译/待词条化清单.tsv 供人工分批消化；不阻塞构建）`)
  writeFileSync(join(process.cwd(), '../docs/翻译/待词条化清单.tsv'), hits.join('\n') + '\n')
} else {
  console.log('✓ 硬编码语言串 0 处')
}
