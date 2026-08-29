<template>
  <!-- P2-3（web-design 会话定案）：Monaco 编辑器共享组件（monaco-editor 0.56 已在依赖——
       零新增包；DSL/Python 复用，语言与高度可配。DSL 因子名补全接 /factors 属后续增强） -->
  <div ref="box" :style="{ height: height + 'px', border: '1px solid var(--border-weak)', borderRadius: '4px' }"></div>
</template>
<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
const props = defineProps({
  modelValue: { type: String, default: '' },
  language: { type: String, default: 'plaintext' },
  height: { type: Number, default: 140 },
})
const emit = defineEmits(['update:modelValue'])
const box = ref(null)
let editor = null
onMounted(async () => {
  try {
    const monaco = await import('monaco-editor')
    editor = monaco.editor.create(box.value, {
      value: props.modelValue, language: props.language, theme: 'vs-dark',
      fontSize: 13, minimap: { enabled: false }, lineNumbers: 'on',
      automaticLayout: true, scrollBeyondLastLine: false, tabSize: 2,
    })
    editor.onDidChangeModelValue(() => emit('update:modelValue', editor.getValue()))
  } catch (e) { console.warn('Monaco 加载失败，降级 textarea', e) }
})
watch(() => props.modelValue, v => { if (editor && editor.getValue() !== v) editor.setValue(v || '') })
onBeforeUnmount(() => { try { editor?.dispose() } catch {} })
</script>
