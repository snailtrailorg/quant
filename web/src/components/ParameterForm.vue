<template>
  <div>
    <div v-for="(def, i) in defs" :key="i" style="margin-bottom: 12px">
      <el-form-item :label="def.label || def.name">
        <div style="display: flex; align-items: center; gap: 8px; width: 100%">
          <el-switch v-if="def.type === 'boolean'" v-model="vals[def.name]" />
          <el-select v-else-if="def.type === 'select'" v-model="vals[def.name]" style="width: 200px">
            <el-option v-for="opt in def.options" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
          <el-input-number v-else-if="def.type === 'number'" v-model="vals[def.name]"
            :min="def.min" :max="def.max" :step="def.step || 1" style="width: 200px" />
          <el-input v-else v-model="vals[def.name]" :placeholder="def.placeholder || ''" style="width: 200px" />
          <span style="color: #999; font-size: 12px">{{ def.description || '' }}</span>
        </div>
      </el-form-item>
    </div>
    <div v-if="!defs || defs.length === 0" style="color: #999; font-size: 12px; padding-left: 100px">
      该策略未定义可配置参数
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  defs: { type: Array, default: () => [] },
  modelValue: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue'])

const vals = ref({ ...props.modelValue })

// 当 defs 变化时，用默认值填充
watch(() => props.defs, (newDefs) => {
  const newVals = {}
  for (const def of (newDefs || [])) {
    newVals[def.name] = props.modelValue[def.name] !== undefined
      ? props.modelValue[def.name]
      : def.default
  }
  vals.value = newVals
  emit('update:modelValue', newVals)
}, { immediate: true, deep: true })

watch(vals, (v) => emit('update:modelValue', v), { deep: true })
</script>