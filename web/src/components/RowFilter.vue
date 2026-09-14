<template>
  <!-- 批23：行筛选统一组件（用户裁定：行筛选下拉改 Filter 图标钮+popover 复选组多选）。
       语义契约：「全部」= 空数组（无过滤）；已选值数组经 v-model 双向同步；
       已选非空时按钮加角标（数字=生效筛选数），与 ColumnSettings（Operation 图标=列显隐）成对：
       本组件管"行"（数据维度筛选），放 header 右侧动作区（动作组统一序：筛选→…→列设置）。 -->
  <el-popover :width="240" trigger="click">
    <template #reference>
      <el-badge :value="modelValue.length" :hidden="!modelValue.length" class="rf-badge">
        <IconBtn :icon="Filter" :title="title" />
      </el-badge>
    </template>
    <el-checkbox :model-value="modelValue.length === 0" class="rf-opt rf-all" @update:model-value="onAll">
      {{ t('common.all') }}
    </el-checkbox>
    <el-divider style="margin: var(--sp-1) 0" />
    <div style="max-height: 260px; overflow-y: auto">
      <el-checkbox-group v-model="modelValue">
        <el-checkbox v-for="o in options" :key="o.value" :value="o.value" class="rf-opt">{{ o.label }}</el-checkbox>
      </el-checkbox-group>
    </div>
  </el-popover>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { Filter } from '@element-plus/icons-vue'
import IconBtn from './IconBtn.vue'

const props = defineProps({
  /** 筛选项 [{value, label}]——value 对齐数据字段实际枚举 */
  options: { type: Array, required: true },
  /** 钮 tooltip（与 ColumnSettings 的「列设置」区分，常规传 t('common.filter')） */
  title: { type: String, default: '' },
})
const modelValue = defineModel({ type: Array, default: () => [] })

const { t } = useI18n()

// 「全部」快捷项：勾=清空（无过滤）；反勾=维持空数组（盲审 B-P3-3：反勾直觉=取消筛选；
// 显式全选交由 checkbox-group 逐项勾）——反勾时 checkbox 本身会脱离勾态，无需处理
const onAll = (checked) => { if (checked) modelValue.value = [] }
</script>

<style scoped>
.rf-opt { display: block; height: 26px; line-height: 26px; margin-right: 0; width: 100% }
/* 「全部」独立于 checkbox-group 之上（不被 group 值裹挟） */
.rf-all { padding: 0 8px; height: 26px; line-height: 26px; margin-right: 0; width: 100% }
/* 角标贴圆钮右上（el-badge sup 绝对定位于包裹层） */
.rf-badge { display: inline-flex; align-items: center; }
</style>
