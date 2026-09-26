<template>
  <!-- 批55-0:通道表格壳(拖拽列+reorder 乐观重排通用层——第七份复制止于此,弹窗不抽:凭证/枚举异构真实)。
       用法: <ChannelTableShell :rows="rows" storage-key="xx" :drag-title-key="'a.b'" @reorder="ids => api(ids)">
             列内容走默认插槽(调用方 el-table-column 依序追加,本壳只出手柄列)。
       批55b 扩展:can-drag 谓词(可选)——混合行场景(数据页含交易域行,可见但不可拖):
       手柄按行隐藏;drop 仅在可拖行间生效,重排只动可拖子集,不可拖行原位不动。 -->
  <div>
    <div v-if="noteKey" class="order-note">{{ t(noteKey) }}</div>
    <TableShell :data="rows" :storage-key="storageKey" row-key="id">
      <el-table-column :label="t(dragTitleKey)" width="46">   <!-- :label 保留供列宽持久化键(批48 快审 A-P1-2) -->
        <template #header><el-icon :title="t(dragTitleKey)" :size="16"><Menu /></el-icon></template>
        <template #default="{ row, $index }">
          <span v-if="!canDrag || canDrag(row)" class="drag-handle" :draggable="true"
                :title="t(dragTitleKey)"
                @dragstart="$emit('dragstart', $index); dragIdx = $index" />
        </template>
      </el-table-column>
      <slot />   <!-- 调用方列(名称列若需作 drop 目标,监听 @dragstart 自行处理或用具名 slot) -->
    </TableShell>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Menu } from '@element-plus/icons-vue'
import TableShell from './TableShell.vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  storageKey: { type: String, required: true },
  dragTitleKey: { type: String, required: true },   // 手柄 title+列头 tooltip 词条键
  noteKey: { type: String, default: '' },           // 表上方排序说明行(空=不渲染)
  reorder: { type: Function, required: true },      // async (ids) => await api(ids)
  canDrag: { type: Function, default: null },       // 批55b:行级可拖谓词(null=全部可拖)
})
const emit = defineEmits(['reorder-failed', 'dragstart'])
const { t } = useI18n()
let dragIdx = -1

const _canDrag = (r) => !props.canDrag || props.canDrag(r)

const onDrop = async (i) => {
  if (dragIdx < 0 || dragIdx === i) return
  const rowsRef = props.rows
  if (!rowsRef[dragIdx] || !rowsRef[i]) { dragIdx = -1; return }   // 盲审 B-P2-8:load 替换后 index 越界的时序防御
  if (!_canDrag(rowsRef[dragIdx]) || !_canDrag(rowsRef[i])) { dragIdx = -1; return }
  // 可拖子集内重排（不可拖行原位不动——批55b 混合行）
  const sub = rowsRef.filter(_canDrag)
  const from = sub.indexOf(rowsRef[dragIdx])
  const to = sub.indexOf(rowsRef[i])
  sub.splice(to, 0, sub.splice(from, 1)[0])
  const arr = [...rowsRef]
  let k = 0
  arr.forEach((r, idx) => { if (_canDrag(r)) arr[idx] = sub[k++] })
  dragIdx = -1
  try {
    // 载荷=全量有序集（批 66a 修：子集载荷与全量校验端点结构性冲突 400——interfaces_reorder
    // 要求 ids=全部行；不可拖行原位不动已由上面 splice 保证，全量序自然保持其相对位置）
    await props.reorder(arr.map(r => r.id))
    rowsRef.splice(0, rowsRef.length, ...arr)   // 乐观落地(调用方 rows 须响应式数组)
  } catch (e) {
    emit('reorder-failed', e)   // 失败回滚由调用方重拉(load)
  }
}
defineExpose({ onDrop })
</script>

<style scoped>
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
.drag-handle { display: inline-block; width: 12px; height: 10px; cursor: grab;
               background: linear-gradient(180deg, var(--text-secondary) 0 2px, transparent 2px 4px, var(--text-secondary) 4px 6px, transparent 6px 8px, var(--text-secondary) 8px 10px);
               opacity: .55; }
.drag-handle:active { cursor: grabbing; }
</style>
