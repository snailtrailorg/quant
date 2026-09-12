<template>
  <!-- 批17 17A：el-table 列宽拖拽+持久化包装（用户裁定：全站可用+保存）
       机制：恒 border（EP 拖拽手柄激活条件）+ header-dragend 落 localStorage + 挂载回放
       + 双击表头=该列回档位默认宽（AG Grid auto-fit 简化版——EP 无此原生事件，th 委托实现）。
       迁移 = <el-table → <TableShell storage-key="语义键"（必填——同页多表/多页签防串档）。
       注意：回放/重置走 EP store.states.columns（内部 API，EP 升级需回归本批测试）。 -->
  <el-table ref="tableRef" v-bind="$attrs" border @header-dragend="onDrag">
    <slot />
  </el-table>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'

defineOptions({ inheritAttrs: false })   // 显式 v-bind="$attrs" 透传（含事件/指令），防根双绑

const props = defineProps({
  storageKey: { type: String, required: true },   // 语义键：'sync-config'/'tasks'/...（localStorage 前缀 colw.）
})

const tableRef = ref(null)
const storeId = 'colw.' + props.storageKey

const loadWidths = () => {
  try {
    const m = JSON.parse(localStorage.getItem(storeId) || '{}')
    return (m && typeof m === 'object') ? m : {}
  } catch { return {} }
}
const saveWidths = (m) => localStorage.setItem(storeId, JSON.stringify(m))
const isResizableCol = c => !c.type || c.type === 'default'   // selection/expand 不记忆
const cols = () => tableRef.value?.store?.states?.columns || []

// EP 2.14 签名 (newWidth, oldWidth, column, event)——按 property||label 记键（无 prop 的模板列回退 label）
const onDrag = (newWidth, _old, column) => {
  if (!column || !isResizableCol(column)) return
  const k = column.property || column.label
  if (!k) return
  const widths = loadWidths()
  widths[k] = Math.round(newWidth)
  saveWidths(widths)
}

const applySaved = () => {
  const widths = loadWidths()
  if (!Object.keys(widths).length) return
  let touched = false
  for (const c of cols()) {
    if (!isResizableCol(c)) continue
    const w = widths[c.property || c.label]
    if (Number.isFinite(w) && w >= 60) { c.width = w; c.realWidth = w; touched = true }
  }
  if (touched) tableRef.value?.doLayout?.()
}

// 双击表头=清除该列用户宽，回声明 min-width（档位默认）。th 类名含 column_<id>——按此对位。
const onDbl = (e) => {
  const th = e.target.closest?.('th')
  if (!th) return
  const col = cols().find(c => th.classList.contains(`column_${c.id}`) || th.className.includes(`column_${c.id}`))
  if (!col || !isResizableCol(col)) return
  const k = col.property || col.label
  const widths = loadWidths()
  if (!(k in widths)) return   // 无用户覆盖，双击无操作
  delete widths[k]
  saveWidths(widths)
  col.width = col.minWidth || col.width
  col.realWidth = col.minWidth || col.realWidth
  tableRef.value?.doLayout?.()
}

onMounted(async () => {
  await nextTick()   // 列注册完成后回放
  applySaved()
  tableRef.value?.$el?.addEventListener('dblclick', onDbl)
})
onUnmounted(() => tableRef.value?.$el?.removeEventListener('dblclick', onDbl))
</script>
