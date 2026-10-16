<template>
  <!-- 批23：行筛选统一组件（Filter 图标钮+popover 复选组多选）。
       批24 迭代十六：分组模式——groups=[{key,label,options}]（一钮多列筛选，下拉按列分组显示），
       modelValue=对象 {key: [选中值]}；「全部」=空对象。单组模式（options）批23 语义不变。
       动态选项（module/actor 等非固定枚举）由调用方从数据 distinct 预取后传入。
       语义契约：空 modelValue=全显（5 消费页一致）；有勾选时底部「清除筛选」钮（批 125
       替代原「全部」复选框+角标——语义歧义/反向复选框/数学冗余三缺陷，用户裁定）；
       与 ColumnSettings（Operation=列显隐）成对：本组件管"行"（数据维度筛选），
       放 header 右侧动作区（统一序：筛选→…→列设置）。 -->
  <el-popover :width="260" trigger="click">
    <template #reference>
      <IconBtn :icon="Filter" :title="title" />
    </template>
    <div style="max-height: 300px; overflow-y: auto">
      <!-- 单组模式（批23 兼容） -->
      <el-checkbox-group v-if="!groups" :model-value="modelValue" @update:model-value="v => emit('update:modelValue', v)">
        <el-checkbox v-for="o in options" :key="o.value" :value="o.value" class="rf-opt">{{ o.label }}</el-checkbox>
      </el-checkbox-group>
      <!-- 分组模式（迭代十六） -->
      <template v-else>
        <div v-for="g in groups" :key="g.key">
          <div class="rf-group-label">{{ g.label }}</div>
          <el-checkbox-group :model-value="modelValue[g.key] || []" @update:model-value="v => onGroup(g.key, v)">
            <el-checkbox v-for="o in g.options" :key="o.value" :value="o.value" class="rf-opt">{{ o.label }}</el-checkbox>
          </el-checkbox-group>
        </div>
      </template>
    </div>
    <el-divider style="margin: var(--sp-1) 0" />
    <!-- 批 125 用户裁定：原「全部」复选框三缺陷——语义歧义（勾≠全勾而是清空）/
         反向复选框（勾上=其他全灭视觉打架）/全选与全清数学等价（只需一个真动作）。
         业界印证：GitHub/Gmail/EP 官方面板均用「清除」文字钮。有勾选才显示。 -->
    <el-button v-if="selCount" text size="small" class="rf-clear" @click="onClear">
      {{ t('common.clearFilter') }}（{{ selCount }}）
    </el-button>
  </el-popover>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Filter } from '@element-plus/icons-vue'
import IconBtn from './IconBtn.vue'

const emit = defineEmits(['update:modelValue'])

const props = defineProps({
  /** 单组模式筛选项 [{value, label}]（批23 兼容；与 groups 二选一） */
  options: { type: Array, default: () => [] },
  /** 分组模式 [{key, label, options}]——key=数据字段/modelValue 对象键（迭代十六） */
  groups: { type: Array, default: null },
  /** 钮 tooltip（常规传 t('common.filter')） */
  title: { type: String, default: '' },
})
// 单组=modelValue=[值]；分组=modelValue={key:[值]}
const modelValue = defineModel({ type: [Array, Object], default: () => [] })

const { t } = useI18n()

const selCount = computed(() => props.groups
  ? Object.values(modelValue.value || {}).reduce((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0)
  : modelValue.value.length)

// 清除筛选（单组 []；分组 {}）——原「全部」复选框的文字钮化（批 125）；
// 空=全显契约不变（5 消费页零改动）；popover 保持开（清完常要立刻选新项）
const onClear = () => emit('update:modelValue', props.groups ? {} : [])
const onGroup = (key, vals) => {
  emit('update:modelValue', { ...modelValue.value, [key]: vals })
}

</script>

<style scoped>
.rf-opt { display: block; height: 26px; line-height: 26px; margin-right: 0; width: 100% }
/* 清除筛选钮：占满宽左对齐（文字钮，非复选框——承载动作非状态） */
.rf-clear { width: 100%; justify-content: flex-start; padding: 0 8px; }
/* 分组标签（迭代十六）：列名小标题 */
.rf-group-label { font-size: var(--fs-foot); font-weight: 600; color: var(--text-secondary); padding: var(--sp-1) 8px 2px; }
</style>
