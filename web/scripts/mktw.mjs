import { readFileSync, writeFileSync } from 'fs'
import OpenCC from 'opencc-js'

const loc = readFileSync('src/locales/index.js', 'utf8')
const mod = { exports: {} }
new Function('module', 'exports', loc.replace(/export default/, 'module.exports ='))(mod, mod.exports)
const zh = mod.exports.zh

const flat = (o, pre = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object') ? flat(v, pre + k + '.') : [[pre + k, String(v)]])
const rows = flat(zh)

const conv = OpenCC.Converter({ from: 'cn', to: 'twp' })
const out = ['# 繁体中文（zh-tw）词典翻译工作稿 — 批 167', '',
  '> 本文件由 OpenCC s2twp（简→繁·台湾用语词汇级）首遍生成。',
  '> 你的任务：逐行复核第三列（机器繁体预译），按台湾惯用语做**语义修订**，改第三列即可。',
  '> 格式：TSV 三列 = 键路径 <TAB> 简体原文（只读对照，勿改） <TAB> 繁体译文（修订此列）。',
  '> 词汇差异示例（机器已按 OpenCC 台湾词汇表处理大半，但金融语境需人工把关）：',
  '>   网络→網路 ✓ 软件→軟體 ✓ 数据→資料 ✓ 智能→智慧 ✓ 默认→預設 ✓',
  '>   但注意本系统金融语义：数据源（data source）建议「資料源」、收益率建议「報酬率」（台湾财经惯用）、',
  '>   回测→回測（可保留）、持仓→持倉（两岸同）、止损→停損（台湾惯用）、做多/做空→做多/做空（保留）。',
  '>   标点：简体引号“” 请改为「」；顿号、保留。',
  '> 完成后整文件回传，我方合入 locales 并跑三语守门。', '']
for (const [k, v] of rows) {
  out.push([k, v, conv(v)].join('\t'))
}
writeFileSync('/home/bernard/Projects/quant/docs/翻译/zh-tw-词典工作稿.tsv', out.join('\n'))
console.log('rows:', rows.length)
