<template>
  <!-- 批36b-β：JSON 参数输入统一组件（Factors 默认参数/Backtest per-symbol 两处收编）——
       textarea+失焦校验+行内错误（common.jsonInvalid 文案师终稿）；v-model 恒字符串兼容现有提交链 -->
  <div style="width: 100%">
    <el-input :model-value="modelValue" type="textarea" :rows="rows" :placeholder="placeholder"
              @update:model-value="$emit('update:modelValue', $event)" @blur="validate" />
    <div v-if="err" class="json-err">{{ t('common.jsonInvalid') }}</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
const props = defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  rows: { type: Number, default: 4 },
})
const emit = defineEmits(['update:modelValue', 'valid'])
const { t } = useI18n()
const err = ref(false)
const validate = () => {
  const v = (props.modelValue || '').trim()
  err.value = false
  if (!v) { emit('valid', true); return true }
  try {
    JSON.parse(v)
    emit('valid', true)
    return true
  } catch {
    err.value = true
    emit('valid', false)
    return false
  }
}
defineExpose({ validate })
</script>
<style scoped>
.json-err { font-size: var(--fs-foot); color: var(--critical); margin-top: 2px; line-height: 1.4; }
</style>
