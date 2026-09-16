<template>
  <!-- 批17 17A：el-table 列宽拖拽+持久化包装（用户裁定：全站可用+保存）
       机制：恒 border（EP 拖拽手柄激活条件）+ header-dragend 落 localStorage + 挂载回放
       + 双击表头=该列回声明宽（AG Grid auto-fit 简化版；盲审A-P0 修：th 类名=完整 column.id 做 token，
         直接 classList.contains(c.id) 命中；盲审A-P1-4 修：挂载时记声明宽快照，双击回快照而非 EP 默认 minWidth）
       + 列集合变化（ColumnSettings 显隐切换）重放存档宽（盲审 A/B 同判）
       迁移 = <el-table → <TableShell storage-key="语义键"（必填）:loading="x"（替代 v-loading 指令）。
       已知限制（盲审B-P2-1 裁定）：无 prop 模板列以渲染后 label 记键，切语言后该列存档失配（宽回默认，非破坏）。
       回放走 EP store.states（内部 API，EP 升级需回归本批测试）。
       批32：fill（撑满可用空间表内滚）/infinite（滚到底 load-more）两 opt-in——见 props 注释；
       infinite 依赖 EP el-table 公开 @scroll 事件（2.14.3 实证 event.target=滚动 wrap），
       append slot 由 EP 渲染进滚动区（高度天然计入）——同为 EP 行为面，升级需回归。 -->
  <el-table ref="tableRef" v-bind="tableAttrs" border v-loading="loading" @header-dragend="onDrag">
    <slot />
    <template v-if="moreText" #append>
      <div class="ts-more">{{ moreText }}</div>
    </template>
  </el-table>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted, watch, useAttrs } from 'vue'

defineOptions({ inheritAttrs: false })   // 显式 v-bind="$attrs" 透传（含事件），防根双绑

const props = defineProps({
  storageKey: { type: String, required: true },   // 语义键（localStorage 前缀 colw.）
  loading: { type: Boolean, default: undefined },  // 盲审A-P2-10：v-loading 指令在组件根=dev 警告，收 prop 内层化
  // 批32 fill：撑满可用空间（el-main 底 − 表格顶 − reserve），max-height 语义=数据少自然高/多则表内滚。
  // reserve 默认 40（≥el-main padding-bottom 30，小了残留外滚——批32 盲审 A-P2-1）；页有表下元素时调大。
  fill: { type: Boolean, default: false },
  fillReserve: { type: Number, default: 40 },
  // 批32 infinite：滚到底（距底 ≤60px）emit load-more（父层 loading 自行守护去抖）；隐含 fill。
  // moreText 经 append slot 渲染在滚动区内（"加载中/没有更多"——高度天然计入，免 fill 扣减）。
  infinite: { type: Boolean, default: false },
  moreText: { type: String, default: '' },
})
const emit = defineEmits(['load-more'])

// fill 生效时忽略外部传入的 height/max-height（批32 契约——B-P2-7：两值并存 EP wrapStyle 语义混乱）
// 注：useAttrs() 返回响应式对象本身（非 ref）——此处不可 .value（P0 实锤：.value=undefined
// 展开成空 attrs，:data/loading/style 全丢=全站表格空、零报错、构建绿的三重盲区）
const attrsProxy = useAttrs()
const tableAttrs = computed(() => {
  const a = { ...attrsProxy }
  if (props.fill || props.infinite) {
    delete a.height
    delete a['max-height']
    delete a.maxHeight
    a.maxHeight = availHeight.value || undefined
  }
  return a
})

const tableRef = ref(null)
const availHeight = ref(0)
let ro = null

const recompute = () => {
  const el = tableRef.value?.$el
  if (!el) return
  const main = el.closest?.('.el-main')
  const anchorBottom = main ? main.getBoundingClientRect().bottom : window.innerHeight   // 锚 el-main 非 viewport（fixed footer 遮挡巧合非设计——批32 A-P2-2）
  const top = el.getBoundingClientRect().top
  const avail = Math.floor(anchorBottom - top - props.fillReserve)
  availHeight.value = Math.max(240, avail)   // 地板 240：小屏防挤没
}

// 批32：滚到底触发（A-P0-2 修正：EP el-table 的 scroll 是组件自定义事件，载荷=纯对象
// {scrollTop,scrollLeft} 无 target——模板 @scroll 拿不到滚动容器。改为挂载期对 $el 捕获监听
// （捕获截获子树内滚动容器，e.target=滚动 wrap，含 scrollHeight/clientHeight 全量信息；
// 不 querySelector、不依赖 EP 载荷形状；scroll 不冒泡=捕获是必需）
let lastFire = 0
const onScroll = (e) => {
  if (!props.infinite) return
  const t = e?.target
  if (!t) return
  if (t.scrollTop + t.clientHeight >= t.scrollHeight - 60) {
    const now = Date.now()
    if (now - lastFire < 200) return   // 高频滚动事件级去抖（跨请求去抖由父层 loading 守护）
    lastFire = now
    emit('load-more')
  }
}
const storeId = 'colw.' + props.storageKey
const declaredWidths = new Map()   // 盲审A-P1-4：声明宽快照（prop→width/min-width 原值）——双击回档真值

// 批23：透出内层 el-table 清选（selection+reserve-selection 批删后清勾选，
// script setup 组件默认闭合，不 expose 则父层拿不到 el-table 方法）
defineExpose({ clearSelection: () => tableRef.value?.clearSelection?.() })

const loadWidths = () => {
  try {
    const m = JSON.parse(localStorage.getItem(storeId) || '{}')
    return (m && typeof m === 'object') ? m : {}
  } catch { return {} }
}
const saveWidths = (m) => localStorage.setItem(storeId, JSON.stringify(m))
const isResizableCol = c => !c.type || c.type === 'default'   // selection/expand 不记忆
const cols = () => {
  const cs = tableRef.value?.store?.states?.columns
  return Array.isArray(cs) ? cs : []   // 迭代十六 hotfix：EP 内部结构异常/HMR 版本错乱时防御——不可迭代即空
}
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
  // 批32：fill/infinite 高度观测——表格自身 RO（页签 v-if 激活 0→实高自然触发重算）+ window resize 兜底；
  // infinite 滚动=捕获监听（A-P0-2：EP scroll 自定义事件载荷无 target，见 onScroll 注释）
  if (props.fill || props.infinite) {
    recompute()
    ro = new ResizeObserver(() => recompute())
    ro.observe(tableRef.value?.$el)
    window.addEventListener('resize', recompute)
  }
  if (props.infinite) tableRef.value?.$el?.addEventListener('scroll', onScroll, true)
  // 盲审A-P2-5/B-P2-2：列集合变化（显隐切换重建 column 对象）→ 重放存档宽
  watch(() => cols().length, async (_n, o) => {
    if (o === undefined) return
    await nextTick()
    applySaved()
  })
})
onUnmounted(() => {
  tableRef.value?.$el?.removeEventListener('dblclick', onDbl)
  tableRef.value?.$el?.removeEventListener('scroll', onScroll, true)
  ro?.disconnect()
  window.removeEventListener('resize', recompute)
})
</script>

<style scoped>
/* 批32：append slot 懒加载提示行（滚动区内） */
.ts-more { text-align: center; padding: 8px 0; color: var(--text-secondary); font-size: var(--fs-foot); }
</style>
