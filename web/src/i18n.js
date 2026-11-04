import { createI18n } from 'vue-i18n'
import messages from './locales/index.js'

// 语言码 → 本族语名称（菜单显示用）。批167 单源化：**语言集从 messages keys 派生**——
// locales 加新语言域即自动出现在菜单（零 UI 改动）；本族语名仅此一处登记。
const NATIVE_NAMES = { zh: '简体中文', 'zh-tw': '繁體中文', en: 'English' }
export const LANGUAGES = Object.keys(messages)
  .map(code => ({ code, label: NATIVE_NAMES[code] || code }))

const savedLang = localStorage.getItem('lang')

// 浏览器语言检测：遍历用户偏好语言列表，匹配已实现语言；都不匹配回落 en（国际通用缺省）
function detectBrowserLang() {
  const available = Object.keys(messages)
  const preferred = (navigator.languages || [navigator.language || 'en'])
  for (const raw of preferred) {
    const rawL = String(raw).toLowerCase()
    const full = rawL.replace('_', '-')
    if (available.includes(full)) return full            // 精确命中（zh-tw）
    const short = rawL.split('-')[0]
    // zh 系细分：zh-tw/zh-hk → zh-tw（繁体优先归繁）；其余 zh → zh
    if (short === 'zh') return (rawL.includes('tw') || rawL.includes('hk')) ? 'zh-tw' : 'zh'
    if (available.includes(short)) return short
  }
  return 'en'
}

const i18n = createI18n({
  legacy: false,
  locale: savedLang || detectBrowserLang(),
  fallbackLocale: 'en',
  messages,
})

export default i18n
export function setLang(lang) {
  i18n.global.locale.value = lang
  localStorage.setItem('lang', lang)
}
