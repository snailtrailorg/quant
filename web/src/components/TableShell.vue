<template>
  <!-- 批17 17A：el-table 列宽拖拽+持久化包装（用户裁定：全站可用+保存）
       机制：恒 border（EP 拖拽手柄激活条件）+ header-dragend 落 localStorage + 挂载回放
       + 双击表头=该列回声明宽（AG Grid auto-fit 简化版；盲审A-P0 修：th 类名=完整 column.id 做 token，
         直接 classList.contains(c.id) 命中；盲审A-P1-4 修：挂载时记声明宽快照，双击回快照而非 EP 默认 minWidth）
       + 列集合变化（ColumnSettings 显隐切换）重放存档宽（盲审 A/B 同判）
       迁移 = <el-table → <TableShell storage-key="语义键"（必填）:loading="x"（替代 v-loading 指令）。
       已知限制（盲审B-P2-1 裁定）：无 prop 模板列以渲染后 label 记键，切语言后该列存档失配（宽回默认，非破坏）。
       回放走 EP store.states.columns（内部 API，EP 升级需回归本批测试）。 -->
  <el-table ref="tableRef" v-bind="$attrs" border v-loading="loading" @header-dragend="onDrag">
    <slot />
  </el-table>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted, watch } from 'vue'

defineOptions({ inheritAttrs: false })   // 显式 v-bind="$attrs" 透传（含事件），防根双绑

const props = defineProps({
  storageKey: { type: String, required: true },   // 语义键（localStorage 前缀 colw.）
  loading: { type: Boolean, default: undefined },  // 盲审A-P2-10：v-loading 指令在组件根=dev 警告，收 prop 内层化
})

const tableRef = ref(null)
const storeId = 'colw.' + props.storageKey
const declaredWidths = new Map()   // 盲审A-P1-4：声明宽快照（prop→width/min-width 原值）——双击回档真值

const loadWidths = () => {
  try {
    const m = JSON.parse(localStorage.getItem(storeId) || '{}')
    return (m && typeof m === 'object') ? m : {}
  } catch { return {} }
}
const saveWidths = (m) => localStorage.setItem(storeId, JSON.stringify(m))
const isResizableCol = c => !c.type || c.type === 'default'   // selection/expand 不记忆
const cols = () => tableRef.value?.store?.states?.columns || []
const colKey = c => c.property || c.label

const snapshotDeclared = () => {
  for (const c of cols()) {
    if (!isResizableCol(c) || declaredWidths.has(c)) continue
    declaredWidths.set(c, { width: c.width, minWidth: c.minWidth })   // 值快照（引用会读到回放/拖拽后污染值）
  }
}

// EP 2.14 签名 (newWidth, oldWidth, column, event)——按 property||label 记键；存档下限 60 对齐回放（盲审B-P3-2）
const onDrag = (newWidth, _old, column) => {
  if (!column || !isResizableCol(column)) return
  const k = colKey(column)
  if (!k) return
  const widths = loadWidths()
  widths[k] = Math.max(60, Math.round(newWidth))
  saveWidths(widths)
}

const applySaved = () => {
  const widths = loadWidths()
  if (!Object.keys(widths).length) return
  let touched = false
  for (const c of cols()) {
    if (!isResizableCol(c)) continue
    const w = widths[colKey(c)]
    if (Number.isFinite(w) && w >= 60) { c.width = w; c.realWidth = w; touched = true }
  }
  if (touched) tableRef.value?.doLayout?.()
}

// 双击表头=清该列用户宽，回声明宽快照（A-P0：th 类名含完整 column.id token；A-P1-4：回快照值）
const onDbl = (e) => {
  const th = e.target.closest?.('th')
  if (!th) return
  const col = cols().find(c => th.classList.contains(c.id))
  if (!col || !isResizableCol(col)) return
  const k = colKey(col)
  const widths = loadWidths()
  if (!(k in widths)) return   // 无用户覆盖，双击无操作
  delete widths[k]
  saveWidths(widths)
  const snap = declaredWidths.get(col)
  const w = snap ? (snap.width ?? snap.minWidth) : (col.minWidth || col.width)
  if (Number.isFinite(w) && w >= 40) { col.width = w; col.realWidth = w }
  tableRef.value?.doLayout?.()
}

onMounted(async () => {
  await nextTick()   // 列注册完成后：先记声明快照（未污染态）再回放
  snapshotDeclared()
  applySaved()
  tableRef.value?.$el?.addEventListener('dblclick', onDbl)
  // 盲审A-P2-5/B-P2-2：列集合变化（显隐切换重建 column 对象）→ 重放存档宽
  watch(() => cols().length, async (_n, o) => {
    if (o === undefined) return
    await nextTick()
    applySaved()
  })
})
onUnmounted(() => tableRef.value?.$el?.removeEventListener('dblclick', onDbl))
</script>
