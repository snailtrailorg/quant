<template>
  <!-- :locale 批24：EP 内置文案（表格空数据"No Data"/分页/日期选择器等）跟界面语言走——
       此前未绑定导致中文环境显示英文 No Data（用户验收发现） -->
  <el-config-provider size="default" :locale="epLocale">
    <router-view />
    <Footer />
  </el-config-provider>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import Footer from './components/Footer.vue'

const { t, locale } = useI18n()
const epLocale = computed(() => (locale.value === 'zh' ? zhCn : en))
// 标签页标题走 app.title 资源，切语言即时同步（index.html 静态 title 仅作加载前兜底 + SEO）；
// html lang 同步（批24：CSS 语言分支（zh 全角冒号）与无障碍/SEO 共用）
watch(locale, () => {
  document.title = t('app.title')
  document.documentElement.lang = locale.value === 'zh' ? 'zh-CN' : 'en'
}, { immediate: true })
</script>

<style>
body { margin: 0; font-family: var(--font-ui); font-size: var(--fs-body); }
</style>
