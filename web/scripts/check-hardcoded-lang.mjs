import { readFileSync, readdirSync, writeFileSync } from 'fs'
import { join, basename } from 'path'

// 批167：硬编码语言扫描——t() 调用外的 UI 可见中文/英文句子字面量（模板文本节点+JS 字符串）
// 白名单：注释行 / console.log / 正则 / 键名标识符 / 纯标点数字 / 域名 URL / storage key
const SRC = join(process.cwd(), 'src')
// 批167：豁免声明表（行内 i18n-ok 标注的可靠补充——标注在行内会被字符串/转义吞噬的场景在此声明）
// 格式：文件名 → 豁免判据（行号集合或子串）
const HARDCODE_OK = {
  'TableShell.vue': [/防根双绑/],                 // 组件注释（转义引号尾注，非 UI）
  'i18n.js': [/^\s*(zh|'zh-tw'|en):/, /简体中文|繁體中文|English|'简'|'繁'|'EN'/],   // 语言名本体（设计豁免）
  'Factors.vue': [/计算因子值|可用属性|简单移动平均|历史 bar 列表|当前 bar 值/],   // Python 代码示例块（DEFAULT_CODE 模板串内）
  'LiveTask.vue': [/任务启动/],                   // 日志消息逻辑匹配串（前后端 msg 契约）
}
const files = []
const walk = d => { for (const f of readdirSync(d, { withFileTypes: true })) { const p = join(d, f.name); if (f.isDirectory()) walk(p); else if (/\.vue$|\.js$/.test(f.name) && !/locales\/index\.js$/.test(p)) files.push(p) } }   // 批167：locales 词表本身豁免
walk(SRC)

const hits = []
for (const f of files) {
  const base = basename(f)
  const okRes = HARDCODE_OK[base] || []
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
    if (/i18n-ok/.test(rawLine)) {
      // 批167：标注行若开模板串（` 数为奇），豁免至闭合反引号（代码示例整体豁免）
      if ((rawLine.split('`').length - 1) % 2 === 1) {
        let j = i + 1
        while (j < lines.length) {
          if ((lines[j].split('`').length - 1) % 2 === 1) { i = j; break }
          j++
        }
        if (j >= lines.length) i = lines.length
      }
      return
    }
    line = line.replace(/\/\/[^"\n]*$/, '')            // 行内尾注（保守：到引号停）
    if (!line.trim()) return
    if (/console\.|throw new|logger\.|test\(|expect\(/.test(line)) return
    // 模板文本节点里的裸中文（不在 {{ }} / 属性里的中文句子——含 4+ 连续汉字即报）
    for (const m of line.matchAll(/([\u4e00-\u9fff]{4,})/g)) {
      const s = m[1]
      const exempt = okRes.some(re => re.test(rawLine))
      if (exempt) continue
      if (/^[\u4e00-\u9fff]+$/.test(s) && !/t\(/.test(line)) hits.push(`${base}:${i + 1} 模板中文「${s}」`)
      else if (/t\(/.test(line) && !/t\('[^']*'/.test(s) && /['"`]${s}['"`]/.test(line) && !/i18nKey|label_key/.test(line)) hits.push(`${base}:${i + 1} 可疑「${s}」`)
    }
  })
}
if (hits.length) {
  console.log(`⚠ 疑似硬编码语言串 ${hits.length} 处（含注释残渣/数据 doc 类——报告落 docs/翻译/待词条化清单.tsv 供人工分批消化；不阻塞构建）`)
  writeFileSync(join(process.cwd(), '../docs/翻译/待词条化清单.tsv'), hits.join('\n') + '\n')
} else {
  console.log('✓ 硬编码语言串 0 处')
}
