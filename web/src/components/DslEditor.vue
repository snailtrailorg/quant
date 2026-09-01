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

const FIELDS = [
  { id: 'close', doc: '当前 bar 收盘价（标量）' },
  { id: 'high', doc: '当前 bar 最高价（标量）' },
  { id: 'low', doc: '当前 bar 最低价（标量）' },
  { id: 'open', doc: '当前 bar 开盘价（标量；open 与 open_ 等价）' },
  { id: 'open_', doc: '开盘价别名（与 open 等价）' },
  { id: 'volume', doc: '当前 bar 成交量（标量）' },
]
const FUNCS = [
  // 纯标量函数（factor.py _DT_FUNCS 白名单,盲审 B-P2 补齐）
  { id: 'abs', sig: 'abs(x)', doc: '绝对值' },
  { id: 'sum', sig: 'sum(a, b, ...)', doc: '求和（标量）' },
  { id: 'round', sig: 'round(x, ndigits?)', doc: '四舍五入' },
  { id: 'float', sig: 'float(x)', doc: '转浮点' },
  { id: 'int', sig: 'int(x)', doc: '转整数' },
  // 窗口函数（8）
  { id: 'mean', sig: 'mean(field, n)', doc: '窗口 n 根（含当前）均值' },
  { id: 'std', sig: 'std(field, n)', doc: '窗口 n 根标准差' },
  { id: 'max', sig: 'max(field, n)', doc: '窗口 n 根最大值' },
  { id: 'min', sig: 'min(field, n)', doc: '窗口 n 根最小值' },
  { id: 'ema', sig: 'ema(field, n)', doc: '窗口 n 根指数移动均值' },
  { id: 'rsi', sig: 'rsi(field, n)', doc: '窗口 n 根 RSI（0-100）' },
  { id: 'slope', sig: 'slope(field, n)', doc: '窗口 n 根线性斜率' },
  { id: 'avevol', sig: 'avevol(field, n)', doc: '窗口 n 根平均量' },
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
            detail: f.sig, documentation: f.doc, range,
          })),
          ...FIELDS.map(f => ({
            label: f.id, kind: K.Variable, insertText: f.id,
            detail: '字段', documentation: f.doc, range,
          })),
          {
            label: 'mean(close,20) / close - 1',
            kind: K.Snippet, insertText: 'mean(close,20) / close - 1',
            detail: '模板', documentation: '均线偏离度（经典例）', range,
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
