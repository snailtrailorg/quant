<template>
  <router-view />
  <Footer />
</template>

<script setup>
import { watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Footer from './components/Footer.vue'

const { t, locale } = useI18n()
// 标签页标题走 app.title 资源，切语言即时同步（index.html 静态 title 仅作加载前兜底 + SEO）
watch(locale, () => { document.title = t('app.title') }, { immediate: true })
</script>

<style>
body { margin: 0; font-family: 'Segoe UI', Roboto, sans-serif; }
</style>
