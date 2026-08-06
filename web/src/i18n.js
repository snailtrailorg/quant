import { createI18n } from 'vue-i18n'
import messages from './locales/index.js'

const savedLang = localStorage.getItem('lang')
const browserLang = navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'

const i18n = createI18n({
  legacy: false,
  locale: savedLang || browserLang,
  fallbackLocale: 'en',
  messages,
})

export default i18n
export function setLang(lang) {
  i18n.global.locale.value = lang
  localStorage.setItem('lang', lang)
}
