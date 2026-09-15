<template>
  <!-- 批23：header 左标题/右动作组（筛选输入→删除→导出）；表加 selection 批删（confirm 数量回显）。
       筛选=本地 computed 即时生效（原表单+查询/重置按钮退役）；导出 IconBtn 化收进动作组。 -->
  <div>
    <!-- 批24 迭代十六：el-card 退役（Observe 页签卡内防卡中卡）；筛选文字框→RowFilter 分组（用户+动作，均数据 distinct 预取——
         用户裁定：用户相关等非固定下拉提前备好）；删除钮图标化（Delete/DeleteFilled） -->
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: var(--sp-3)">
      <RowFilter v-model="rowFilter" :groups="filterGroups" :title="t('common.filter')" />
      <IconBtn v-if="isAdmin" size="small" :icon="Delete" type="danger" :title="t('common.deleteSelected')"
               :disabled="!selRows.length" @click="onDeleteSelected" />
      <IconBtn v-if="isAdmin" size="small" :icon="DeleteFilled" type="danger" :title="t('common.deleteAll')"
               :disabled="!logs.length" @click="onClearAll" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExportCsv" />
    </div>
    <TableShell ref="tableRef" :data="filteredLogs" storage-key="audit" row-key="id" @selection-change="onSelChange">
      <el-table-column type="selection" width="42" reserve-selection />
      <el-table-column prop="ts" :label="t('common.time')" min-width="160">
        <template #default="{ row }">{{ fmtTime.full(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="actor" :label="t('audit.actor')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="action" :label="t('common.action')" min-width="140" />
      <el-table-column prop="target" :label="t('audit.target')" min-width="140" />
      <el-table-column prop="detail" :label="t('common.detail')" show-overflow-tooltip />
    </TableShell>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import TableShell from '../components/TableShell.vue'
import { fmtTime } from '../utils/fmtTime'
import { getAudit, deleteAudit , apiErr } from '../api'
import IconBtn from '../components/IconBtn.vue'
import RowFilter from '../components/RowFilter.vue'
import { exportCsv as exportCsvUtil } from '../exportCsv'
import { Delete, DeleteFilled, Download } from '@element-plus/icons-vue'
const { t } = useI18n()
const logs = ref([])
// 批24 迭代十六：分组筛选 {actor:[], action:[]}——两维均数据 distinct 预取（actor=用户相关非固定集）
const rowFilter = ref({})
const filterGroups = computed(() => [
  { key: 'actor', label: t('audit.actor'), options: [...new Set(logs.value.map(l => l.actor).filter(Boolean))].sort().map(v => ({ value: v, label: v })) },
  { key: 'action', label: t('common.action'), options: [...new Set(logs.value.map(l => l.action).filter(Boolean))].sort().map(v => ({ value: v, label: v })) },
])
const isAdmin = localStorage.getItem('role') === 'admin'   // 删除端点 require_perm(user_mgmt)=admin 地板
const tableRef = ref(null)
const selRows = ref([])
const onSelChange = (rows) => { selRows.value = rows }
const filteredLogs = computed(() => {
  const { actor = [], action = [] } = rowFilter.value
  return logs.value.filter(l => (!actor.length || actor.includes(l.actor)) && (!action.length || action.includes(l.action)))
})
const load = async () => { try { logs.value = await getAudit() } catch (e) { console.error(e) } }
onMounted(load)

// 批23：批量删（后端留痕 audit_delete；audit 自删留痕行在删后写入不被波及）
const onDeleteSelected = async () => {
  const ids = selRows.value.map(r => r.id)
  try {
    await ElMessageBox.confirm(t('common.confirmDeleteSelected', { n: ids.length }), t('common.confirm'), { type: 'warning' })
    await deleteAudit({ ids })
    ElMessage.success(t('common.deleteSuccess'))
    tableRef.value?.clearSelection()
    await load()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(apiErr(e, t('common.failed'))) }
}
const onClearAll = async () => {
  try {
    await ElMessageBox.confirm(t('common.confirmClearAll'), t('common.confirm'), { type: 'warning' })   // all=true 删全表：数量按全部已载记录回显
    await deleteAudit({ all: true })
    ElMessage.success(t('common.deleteSuccess'))
    tableRef.value?.clearSelection()
    await load()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(apiErr(e, t('common.failed'))) }
}

// P3-5(05 §5.10):审计导出 CSV(合规刚需)
const onExportCsv = () => exportCsvUtil('audit',
  [t('common.time'), t('audit.actor'), t('common.action'), t('audit.target'), t('common.detail')],
  filteredLogs.value.map(a => [a.ts, a.actor, a.action, a.target, a.detail]))
</script>
