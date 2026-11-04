<template>
  <div ref="editorContainer" style="width: 100%; height: 120px; border: 1px solid var(--el-border-color); border-radius: 4px"></div>
</template>
<script setup>
// W6（#2a）：DSL 表达式 Monaco 编辑器——补全/签名/高亮。
// 注册纪律（盲审 A-P1）：语言/补全是 monaco 全局——**模块级单次注册**，组件实例只建
// editor+卸载 dispose（对话框反复开关不叠重复补全）。
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({ modelValue: { type: String, default: '' }, placeholder: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue'])
const editorContainer = ref(null)
let editor = null

// 批167：doc 走词条（dslDoc.* 域——monaco 悬浮文档三语随切）
import i18n from '../i18n'
const t = k => i18n.global.t(k)   // 批167：monaco 文档词条（模块级函数用全局实例）

const FIELDS = [
  { id: 'close', doc: 'dslDoc.f_close' },
  { id: 'high', doc: 'dslDoc.f_high' },
  { id: 'low', doc: 'dslDoc.f_low' },
  { id: 'open', doc: 'dslDoc.f_open' },
  { id: 'open_', doc: 'dslDoc.f_open_' },
  { id: 'volume', doc: 'dslDoc.f_volume' },
]
const FUNCS = [
  // 纯标量函数（factor.py _DT_FUNCS 白名单,盲审 B-P2 补齐）
  { id: 'abs', sig: 'abs(x)', doc: 'dslDoc.abs' },
  { id: 'sum', sig: 'sum(a, b, ...)', doc: 'dslDoc.sum' },
  { id: 'round', sig: 'round(x, ndigits?)', doc: 'dslDoc.round' },
  { id: 'float', sig: 'float(x)', doc: 'dslDoc.float' },
  { id: 'int', sig: 'int(x)', doc: 'dslDoc.int' },
  // 窗口函数（8）
  { id: 'mean', sig: 'mean(field, n)', doc: 'dslDoc.mean' },
  { id: 'std', sig: 'std(field, n)', doc: 'dslDoc.std' },
  { id: 'max', sig: 'max(field, n)', doc: 'dslDoc.max' },
  { id: 'min', sig: 'min(field, n)', doc: 'dslDoc.min' },
  { id: 'ema', sig: 'ema(field, n)', doc: 'dslDoc.ema' },
  { id: 'rsi', sig: 'rsi(field, n)', doc: 'dslDoc.rsi' },
  { id: 'slope', sig: 'slope(field, n)', doc: 'dslDoc.slope' },
  { id: 'avevol', sig: 'avevol(field, n)', doc: 'dslDoc.avevol' },
]

let _registered = false
async function ensureLanguage(monaco) {
  if (_registered) return
  _registered = true
  monaco.languages.register({ id: 'quant-dsl' })
  // tokenizer：运算符对齐 factor.py _DT_OPS 全集（+ - * / ** // % 一元±）
  monaco.languages.setMonarchTokensProvider('quant-dsl', {
    keywords: FUNCS.map(f => f.id),
    fields: FIELDS.map(f => f.id),
    tokenizer: {
      root: [
        [/\d+(\.\d+)?/, 'number'],
        [/[a-zA-Z_][\w]*/, { cases: { '@keywords': 'keyword', '@fields': 'variable', '@default': 'identifier' } }],
        [/\*\*|\/\/|[+\-*/%()]/, 'operator'],
      ],
    },
  })
  monaco.languages.registerCompletionItemProvider('quant-dsl', {
    triggerCharacters: ['.'],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      }
      const K = monaco.languages.CompletionItemKind
      return {
        suggestions: [
          ...FUNCS.map(f => ({
            label: f.id, kind: K.Function, insertText: `${f.id}(`,
            detail: f.sig, documentation: t(f.doc), range,
          })),
          ...FIELDS.map(f => ({
            label: f.id, kind: K.Variable, insertText: f.id,
            detail: t('dslDoc.fieldTag'), documentation: t(f.doc), range,
          })),
          {
            label: 'mean(close,20) / close - 1',
            kind: K.Snippet, insertText: 'mean(close,20) / close - 1',
            detail: t('dslDoc.tplTag'), documentation: t('dslDoc.tplMaDev'), range,
          },
        ],
      }
    },
  })
}

let disposed = false
onMounted(async () => {
  const monaco = await import('monaco-editor')
  await ensureLanguage(monaco)
  if (disposed || !editorContainer.value) return   // 盲审 A-P1:import 期间卸载——分离 DOM 上不建
  editor = monaco.editor.create(editorContainer.value, {
    value: props.modelValue || '',
    language: 'quant-dsl',
    minimap: { enabled: false },
    lineNumbers: 'off',
    scrollBeyondLastLine: false,
    fontSize: 13,
    automaticLayout: true,
    placeholder: props.placeholder || undefined,
  })
  editor.onDidChangeModelContent(() => emit('update:modelValue', editor.getValue()))
})

watch(() => props.modelValue, v => {
  if (editor && v !== editor.getValue()) editor.setValue(v || '')
})

onUnmounted(() => {
  // 实例级 dispose（全局语言注册保留——模块级单次语义）;disposed 拦 import 竞态
  disposed = true
  editor?.dispose()
  editor = null
})
</script>
