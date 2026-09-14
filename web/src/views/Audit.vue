<template>
  <!-- 批23：header 左标题/右动作组（筛选输入→删除→导出）；表加 selection 批删（confirm 数量回显）。
       筛选=本地 computed 即时生效（原表单+查询/重置按钮退役）；导出 IconBtn 化收进动作组。 -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('audit.title') }}</span>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-input v-model="filterActor" :placeholder="t('audit.phActor')" clearable size="small" style="width: 120px" />
          <el-input v-model="filterAction" :placeholder="t('audit.phAction')" clearable size="small" style="width: 140px" />
          <template v-if="isAdmin">
            <el-button size="small" type="danger" plain :disabled="!selRows.length" @click="onDeleteSelected">{{ t('common.deleteSelected') }}</el-button>
            <el-button size="small" type="danger" plain :disabled="!logs.length" @click="onClearAll">{{ t('common.clearAll') }}</el-button>
          </template>
          <IconBtn size="small" :icon="Download" :title="t('audit.exportCsv')" @click="exportCsv" />
        </div>
      </div>
    </template>
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
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import TableShell from '../components/TableShell.vue'
import { fmtTime } from '../utils/fmtTime'
import { getAudit, deleteAudit , apiErr } from '../api'
import IconBtn from '../components/IconBtn.vue'
import { Download } from '@element-plus/icons-vue'
const { t } = useI18n()
const logs = ref([])
const filterActor = ref('')
const filterAction = ref('')
const isAdmin = localStorage.getItem('role') === 'admin'   // 删除端点 require_perm(user_mgmt)=admin 地板
const tableRef = ref(null)
const selRows = ref([])
const onSelChange = (rows) => { selRows.value = rows }
const filteredLogs = computed(() => {
  let r = logs.value
  if (filterActor.value) r = r.filter(l => l.actor?.includes(filterActor.value))
  if (filterAction.value) r = r.filter(l => l.action?.includes(filterAction.value))
  return r
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
const exportCsv = () => {
  // 批16 bug1：auditData→filteredLogs（未定义变量必 ReferenceError）+ username→actor（后端字段名）+ 补 target
  const rows = filteredLogs.value.map(a => [a.ts, a.actor, a.action, a.target, a.detail].map(v => `"${(v || '').toString().replace(/"/g, '""')}"`).join(','))
  const csv = '\ufeff' + ['时间,用户,动作,对象,详情', ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = `audit_${new Date().toISOString().slice(0,10)}.csv`; a.click()
  URL.revokeObjectURL(url)
}
</script>
