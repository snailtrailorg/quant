<template>
  <!-- 批16 列显示配置（盲审 A-P0：数据中心两表 11 列 ≈1570px 溢出——用户可按需隐藏列；
       新列默认隐，配置持久化 localStorage，键=storageKey） -->
  <el-popover :width="240" trigger="click">
    <template #reference>
      <el-button size="small">{{ t('cols.settings') }}</el-button>
    </template>
    <el-checkbox-group v-model="visible">
      <el-checkbox v-for="c in columns" :key="c.key" :value="c.key" class="col-opt">{{ c.label }}</el-checkbox>
    </el-checkbox-group>
    <el-divider style="margin: var(--sp-2) 0" />
    <el-button link type="primary" size="small" @click="resetDefault">{{ t('cols.reset') }}</el-button>
  </el-popover>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  storageKey: { type: String, required: true },
  /** 列定义 [{key, label, hidden?}]——hidden=true 的列默认不显示；不进名单的列（名称/操作）恒显不可配 */
  columns: { type: Array, required: true },
})
const visible = defineModel('visible', { type: Array, default: () => [] })

const { t } = useI18n()
const defaultKeys = computed(() => props.columns.filter(c => !c.hidden).map(c => c.key))

// 初始化：localStorage 优先（剔掉已下线的键），无存档用默认
const stored = (() => {
  try { return JSON.parse(localStorage.getItem(props.storageKey) || '') } catch { return null }
})()
if (Array.isArray(stored) && stored.length) {
  const valid = stored.filter(k => props.columns.some(c => c.key === k))
  visible.value = valid.length ? valid : defaultKeys.value
} else {
  visible.value = defaultKeys.value
}

watch(visible, v => localStorage.setItem(props.storageKey, JSON.stringify(v)), { deep: true })

const resetDefault = () => { visible.value = defaultKeys.value }
</script>

<style scoped>
.col-opt { display: block; height: 26px; line-height: 26px; margin-right: 0; width: 100% }
</style>
