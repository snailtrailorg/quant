<script setup>
// wd-20 §2.1 · 统一状态 Tag（中性灰底+彩点，文字恒 --text-primary——红绿只给数据）
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { statusMeta } from '../utils/status'
const props = defineProps({ value: { type: String, required: true } })
const { t } = useI18n()
const meta = computed(() => statusMeta(props.value, t))
</script>
<template>
  <span class="st" :title="value">{{ meta.zh }}<span :class="['dot', meta.dot]" /></span>
</template>
<style scoped>
.st { display: inline-flex; align-items: center; gap: 5px; padding: 0 8px; height: 22px;
  border-radius: 11px; background: var(--bg-canvas); font-size: var(--fs-foot);
  color: var(--text-primary); white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.dot.success { background: var(--success); }
.dot.warn { background: var(--warn-fill); }
.dot.critical { background: var(--critical); }
.dot.info { background: var(--brand-500); }
.dot.neutral { background: var(--flat); }
</style>
