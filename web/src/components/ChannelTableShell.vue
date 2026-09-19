<template>
  <!-- 批55-0:通道表格壳(拖拽列+reorder 乐观重排通用层——第七份复制止于此,弹窗不抽:凭证/枚举异构真实)。
       用法: <ChannelTableShell :rows="rows" storage-key="xx" :drag-title-key="'a.b'" @reorder="ids => api(ids)">
             列内容走默认插槽(调用方 el-table-column 依序追加,本壳只出手柄列)。 -->
  <div>
    <div v-if="noteKey" class="order-note">{{ t(noteKey) }}</div>
    <TableShell :data="rows" :storage-key="storageKey" row-key="id">
      <el-table-column :label="t(dragTitleKey)" width="46">   <!-- :label 保留供列宽持久化键(批48 快审 A-P1-2) -->
        <template #header><el-icon :title="t(dragTitleKey)" :size="16"><Menu /></el-icon></template>
        <template #default="{ $index }">
          <span class="drag-handle" :draggable="true"
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
})
const emit = defineEmits(['reorder-failed', 'dragstart'])
const { t } = useI18n()
let dragIdx = -1

const onDrop = async (i) => {
  if (dragIdx < 0 || dragIdx === i) return
  const arr = [...props.rows]
  const [moved] = arr.splice(dragIdx, 1)
  arr.splice(i, 0, moved)
  dragIdx = -1
  const rowsRef = props.rows
  try {
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
