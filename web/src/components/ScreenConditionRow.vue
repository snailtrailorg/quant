<template>
  <div style="display: flex; gap: var(--sp-1); margin-bottom: var(--sp-1); align-items: center">
    <el-select :model-value="row.field" filterable :placeholder="t('screener.fieldPh')" style="flex: 4; min-width: 0" size="small"
               @update:model-value="onField">
      <el-option-group v-for="g in groups" :key="g" :label="groupLabel(g)">
        <el-option v-for="f in fieldsByGroup(g)" :key="f.name" :value="f.name" :label="f.label"
                   :disabled="f.dead" />
      </el-option-group>
    </el-select>
    <el-select :model-value="row.op" style="flex: 3; min-width: 0" size="small" @update:model-value="emit('upd', {op: $event})">
      <el-option v-for="o in opsFor" :key="o.v" :value="o.v" :label="o.l" />
    </el-select>
    <el-switch v-if="isBool" :model-value="row.value === true" @update:model-value="emit('upd', {value: $event})" />
    <el-input-number v-else :model-value="numVal" :step="0.5" style="flex: 3; min-width: 0" size="small"
                     @update:model-value="emit('upd', {value: $event})" />
    <el-button text size="small" @click="emit('del')">×</el-button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
const props = defineProps({ row: Object, fields: Array })
const emit = defineEmits(['upd', 'del'])
const NUM_OPS = computed(() => [
  { v: '=', l: '=' }, { v: '!=', l: '≠' }, { v: '>', l: '>' }, { v: '>=', l: '≥' },
  { v: '<', l: '<' }, { v: '<=', l: '≤' },
  { v: 'percentile_top', l: t('screener.opPctTop') }, { v: 'percentile_bottom', l: t('screener.opPctBottom') },
])
const groupLabel = g => (t && ['valuation','size','quality','tech','other'].includes(g)) ? t('screener.grp_' + g) : g
const BOOL_OPS = [{ v: '=', l: '=' }]
const cur = computed(() => props.fields.find(f => f.name === props.row.field) || {})
const isBool = computed(() => cur.value.type === 'bool')
const opsFor = computed(() => isBool.value ? BOOL_OPS : NUM_OPS.value)
const numVal = computed(() => (typeof props.row.value === 'number' ? props.row.value : null))
const groups = computed(() => [...new Set(props.fields.map(f => f.group))])
const fieldsByGroup = g => props.fields.filter(f => f.group === g)
const onField = v => {
  const f = props.fields.find(x => x.name === v)
  const patch = { field: v }
  if (f && f.type === 'bool') { patch.op = '='; patch.value = true }
  else if (f) { patch.op = '<'; patch.value = null }
  emit('upd', patch)
}
</script>
