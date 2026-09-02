<script setup>
// wd-20 §2.2 · TabsShell：容器页 tab 壳唯一实现（el-tabs + query ?tab= 同步 + sessionStorage
// 记忆 + label 走 i18n key）。五容器（Screener/DataOps/Observe/Integrations/Settings）归一；
// 新增容器页一律用它（DESIGN.md §8 立法配套）。label 禁中文字面量——传 i18nKey。
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  tabs: { type: Array, required: true },      // [{key, i18nKey, badge?}]
  defaultTab: { type: String, default: '' },
  memoryKey: { type: String, default: '' },   // sessionStorage 记忆键（空=不记忆）
})
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const initial = () => {
  if (route.query.tab) return route.query.tab
  if (props.memoryKey) {
    const m = sessionStorage.getItem(props.memoryKey)
    if (m && props.tabs.some(x => x.key === m)) return m
  }
  return props.defaultTab || props.tabs[0]?.key
}
const tab = ref(initial())
const onTab = (v) => {
  if (props.memoryKey) sessionStorage.setItem(props.memoryKey, v)
  router.replace({ query: { ...route.query, tab: v } })
}
const currentKey = computed(() => (props.tabs.some(x => x.key === tab.value) ? tab.value : props.tabs[0]?.key))
const label = x => t(x.i18nKey)
</script>
<template>
  <el-tabs :model-value="currentKey" @tab-change="onTab">
    <el-tab-pane v-for="x in tabs" :key="x.key" :name="x.key">
      <template #label><b>{{ label(x) }}<span v-if="x.badge" class="tb">{{ x.badge }}</span></b></template>
    </el-tab-pane>
  </el-tabs>
  <slot :tab="currentKey" />
</template>
<style scoped>
.tb { margin-left: 5px; font-size: var(--fs-foot); color: var(--text-secondary); font-weight: 400; }
</style>
