import { createI18n } from 'vue-i18n'
import messages from './locales/index.js'

// 语言注册表：语言码 → 本族语名称（语言切换下拉用；新增语言在 locales 加 messages + 这里登记一项）
export const LANGUAGES = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: 'English' },
]

const savedLang = localStorage.getItem('lang')

// 浏览器语言检测：遍历用户偏好语言列表，匹配已实现语言；都不匹配回落 en（国际通用缺省）
function detectBrowserLang() {
  const available = Object.keys(messages)
  const preferred = (navigator.languages || [navigator.language || 'en'])
  for (const raw of preferred) {
    const short = String(raw).toLowerCase().split('-')[0]
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
