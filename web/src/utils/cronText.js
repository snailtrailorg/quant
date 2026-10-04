// 批 93：cron ↔ 人读描述/结构化模型互转。
// 立法：sync_config.schedule 的**机器真源仍是 cron 字符串**（scheduler/tasks.py 用
// croniter 解析触发），本层只做「看/编」翻译——库里一律存 cron，界面一律人话。
// 纯函数、无依赖（可被任意视图复用），describeCron 接收 t 以走 i18n。

const FIELD_RE = /^\*$|^\*\/\d+$|^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/
const pad = n => String(n).padStart(2, '0')
const one = f => (/^\d+$/.test(f) ? Number(f) : null)
const normDow = d => (d === 0 ? 7 : d)   // cron 0=周日 ⇒ 内部统一 1(一)~7(日)

export function parseCronFields(expr) {
  if (typeof expr !== 'string') return null
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return null
  if (!parts.every(p => FIELD_RE.test(p))) return null
  return { min: parts[0], hour: parts[1], dom: parts[2], mon: parts[3], dow: parts[4] }
}

function dowList(dow) {
  if (dow === '*') return []
  const out = []
  for (const seg of dow.split(',')) {
    const m = seg.match(/^(\d+)-(\d+)$/)
    if (m) for (let d = +m[1]; d <= +m[2]; d++) out.push(normDow(d))
    else out.push(normDow(Number(seg)))
  }
  return [...new Set(out)].sort((a, b) => a - b)
}

// cron → 结构化模型：{freq:'interval',minutes} | {freq:'scheduled',time,days} | {freq:'raw',schedule}
// raw = 超出图形编辑器表达范围（如 0 9 1 1 *），界面退回原文编辑——绝不让用户没法改。
export function cronToModel(expr) {
  const f = parseCronFields(expr)
  if (!f) return { freq: 'raw', schedule: expr }
  const iv = f.min.match(/^\*\/(\d+)$/)
  if (iv && f.hour === '*' && f.dom === '*' && f.mon === '*' && f.dow === '*')
    return { freq: 'interval', minutes: Number(iv[1]) }
  const mi = one(f.min); const ho = one(f.hour)
  if (mi !== null && ho !== null && f.dom === '*' && f.mon === '*')
    return { freq: 'scheduled', time: `${pad(ho)}:${pad(mi)}`, days: dowList(f.dow) }
  return { freq: 'raw', schedule: expr }
}

// 结构化模型 → cron（不加前导零——与既有库内行逐字一致，如 0 9 * * 1-5；Sundays 归一为 0；
// 1~5 连续段写 1-5）
export function modelToCron(m) {
  if (m.freq === 'interval') return `*/${m.minutes} * * * *`
  if (m.freq !== 'scheduled') return m.schedule
  const [h, mi] = String(m.time).split(':').map(Number)
  let dow = '*'
  if (m.days && m.days.length) {
    const ds = [...new Set(m.days.map(normDow))].sort((a, b) => a - b)
    dow = ds.join(',') === '1,2,3,4,5' ? '1-5' : ds.map(d => (d === 7 ? 0 : d)).join(',')
  }
  return `${mi} ${h} * * ${dow}`
}

// cron → 人读描述（解析不了的原样返回，绝不编造）
export function describeCron(expr, t) {
  const f = parseCronFields(expr)
  if (!f) return expr
  const iv = f.min.match(/^\*\/(\d+)$/)
  if (iv && f.hour === '*' && f.dom === '*' && f.mon === '*' && f.dow === '*')
    return t('cronText.everyNMin', { n: iv[1] })
  const mi = one(f.min); const ho = one(f.hour)
  if (mi === null || ho === null) return expr
  if (f.hour === '*' && f.dom === '*' && f.mon === '*' && f.dow === '*')
    return t('cronText.hourlyAt', { m: mi })
  if (f.dom === '*' && f.mon === '*') {
    const time = `${pad(ho)}:${pad(mi)}`
    const days = dowList(f.dow)
    if (!days.length) return t('cronText.dailyAt', { time })
    if (days.join(',') === '1,2,3,4,5') return t('cronText.workdaysAt', { time })
    const label = t('cronText.weekPrefix') + days.map(d => t(`cronText.wd${d}`)).join('/')
    return t('cronText.weekdaysAt', { days: label, time })
  }
  if (/^\d+$/.test(f.dom) && /^\d+$/.test(f.mon) && f.dow === '*')
    return t('cronText.yearlyAt', { m: Number(f.mon), d: Number(f.dom), time: `${pad(ho)}:${pad(mi)}` })
  return expr
}
