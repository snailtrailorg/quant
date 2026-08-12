<template>
  <div ref="editorContainer" class="python-editor" :style="{ height: height + 'px' }"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, shallowRef } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  height: { type: Number, default: 300 },
  readonly: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const editorContainer = ref(null)
let editor = null
let monaco = null

onMounted(async () => {
  try {
    monaco = await import('monaco-editor')
    monaco.languages.register({ id: 'python' })
    editor = monaco.editor.create(editorContainer.value, {
      value: props.modelValue,
      language: 'python',
      theme: 'vs-dark',
      fontSize: 13,
      minimap: { enabled: false },
      lineNumbers: 'on',
      scrollBeyondLastLine: false,
      automaticLayout: true,
      readOnly: props.readonly,
      tabSize: 4,
      insertSpaces: true,
    })
    editor.onDidChangeModelContent(() => {
      emit('update:modelValue', editor.getValue())
    })
  } catch (e) {
    console.error('Monaco Editor 加载失败:', e)
  }
})

watch(() => props.modelValue, (val) => {
  if (editor && val !== editor.getValue()) {
    editor.setValue(val)
  }
})

watch(() => props.readonly, (val) => {
  if (editor) editor.updateOptions({ readOnly: val })
})

onBeforeUnmount(() => {
  if (editor) editor.dispose()
})
</script>

<style scoped>
.python-editor {
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  overflow: hidden;
}
</style>
