// split-font.mjs —— 中文 web 字体分包（W6 #3，2026-09-01）
// 源: Noto Sans SC Regular 静态 otf（系统包, SIL OFL 1.1）→ unicode-range woff2 子集
// 合规: 子集=修改版 → CSS family 别名 "NotoSansSC Web"（RFN 禁用原名——**勿改回**）
// 运行: node scripts/split-font.mjs   （产物预生成入库, 非每次 build）
import { fontSplit } from 'cn-font-split'
import { fileURLToPath } from 'url'
import path from 'path'
import fs from 'fs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC = '/usr/share/fonts/google-noto-sans-sc-fonts/NotoSansSC-Regular.otf'
const OUT = path.resolve(__dirname, '../public/fonts/cjk')
const FAMILY = '"NotoSansSC Web"'

if (!fs.existsSync(SRC)) {
  console.error(`✗ 源字体不存在: ${SRC}（dnf install google-noto-sans-sc-fonts）`)
  process.exit(1)
}
fs.rmSync(OUT, { recursive: true, force: true })

// 默认分包（全字符集;7.4.3 的 subsets/css 自定义选项形状触发 protobuf 断言——
// family 改写用后处理 sed 而非选项注入）
await fontSplit({ input: SRC, outDir: OUT })

// 后处理:①result.css family 改写 ②改名 index.css（tokens.css import 消费）
const cssPath = path.join(OUT, 'result.css')
if (fs.existsSync(cssPath)) {
  let css = fs.readFileSync(cssPath, 'utf8')
  css = css.replace(/font-family:\s*"[^"]*Noto[^"]*"/g, `font-family: ${FAMILY}`)
  css = css.replace(/font-family:\s*'[^']*Noto[^']*'/g, `font-family: ${FAMILY}`)
  css += '\n/* RFN 合规:本文件为 OFL 字体的子集化修改版,家族名用别名禁用 Reserved Font Name */\n'
  fs.writeFileSync(path.join(OUT, 'index.css'), css)
  fs.unlinkSync(cssPath)
}
const files = fs.readdirSync(OUT)
const woff2 = files.filter(f => f.endsWith('.woff2'))
const total = woff2.reduce((a, f) => a + fs.statSync(path.join(OUT, f)).size, 0)
console.log(`✓ ${woff2.length} 分片 / ${(total / 1024 / 1024).toFixed(1)}MB → ${OUT}（全字符集,http2 按需拉取）`)
