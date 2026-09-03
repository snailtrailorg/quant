<template>
  <div class="help-page">
    <el-tabs v-model="activeTab" @tab-change="loadTopic">
      <el-tab-pane v-for="t in tabs" :key="t.key" :label="t.label" :name="t.key" />
    </el-tabs>
    <div v-if="loading" style="padding: 40px; text-align: center">
      <el-skeleton :rows="8" animated />
    </div>
    <div v-else class="markdown-body" v-html="rendered"></div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import api from '../api'
import { marked } from 'marked'

const { t } = useI18n()
const activeTab = ref('index')
const rendered = ref('')
const loading = ref(false)
const tabs = [
  { key: 'index', label: () => t('help.index') },
  { key: 'factors', label: () => t('help.factors') },
  { key: 'strategy', label: () => t('help.strategy') },
  { key: 'backtest', label: () => t('help.backtest') },
  { key: 'live', label: () => t('help.live') },
]

const loadTopic = async (key) => {
  loading.value = true
  try {
    const r = await api.get(`/help/${key}`)
    rendered.value = marked.parse(r.content || '', { async: false })
  } catch (e) {
    rendered.value = `<p style="color:var(--el-color-danger)">${t('help.loadFailed')}</p>`
  } finally { loading.value = false }
}
onMounted(() => loadTopic('index'))
</script>

<style scoped>
.help-page { background: var(--el-bg-color); border-radius: 8px; padding: 20px; }
.markdown-body :deep(h1) { font-size: 22px; margin: 12px 0 16px; }
.markdown-body :deep(h2) { font-size: 18px; margin: 20px 0 10px; border-bottom: 1px solid var(--el-border-color-lighter); padding-bottom: 6px; }
.markdown-body :deep(h3) { font-size: 15px; margin: var(--sp-4) 0 8px; }
.markdown-body :deep(table) { border-collapse: collapse; margin: 12px 0; width: 100%; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid var(--el-border-color-lighter); padding: 6px 10px; font-size: 13px; text-align: left; }
.markdown-body :deep(th) { background: var(--el-fill-color-light); }
.markdown-body :deep(code) { background: var(--el-fill-color); padding: 2px 5px; border-radius: 3px; font-size: 12.5px; }
.markdown-body :deep(pre code) { display: block; padding: 10px; overflow-x: auto; }
.markdown-body :deep(blockquote) { border-left: 3px solid var(--el-color-primary); margin: 10px 0; padding: 4px 12px; color: var(--el-text-color-secondary); }
</style>
