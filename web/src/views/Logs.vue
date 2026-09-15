<template>
  <div>
    <!-- 批24 迭代十六 hotfix4（用户裁定：条目级整合非页面拼接）：邮件发送记录映射为日志条目混排进本表
         （level=status 映射/module=email/msg=收件人+主题+错误），双源合并按时间倒序；
         分组筛选（级别+模块——模块 distinct 预取自然含 email）+导出（合并行） -->
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: var(--sp-3)">
      <RowFilter v-model="rowFilter" :groups="filterGroups" :title="t('common.filter')" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExport" />
    </div>
    <el-alert v-if="noPerm" type="warning" :title="t('log.noPerm')" :closable="false" style="margin-bottom: var(--sp-3)" />
    <TableShell v-else :data="filteredLogs" height="500" storage-key="logs-run">
      <el-table-column prop="ts" :label="t('common.time')" min-width="160">
        <template #default="{ row }">{{ fmtTime.full(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="level" :label="t('log.level')" min-width="80">
        <template #default="{ row }">
          <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="module" :label="t('log.module')" min-width="120" />
      <el-table-column prop="msg" :label="t('log.content')" show-overflow-tooltip />
    </TableShell>

  </div>
</template>

<script setup>
import TableShell from '../components/TableShell.vue'
import RowFilter from '../components/RowFilter.vue'
import IconBtn from '../components/IconBtn.vue'
import { fmtTime } from '../utils/fmtTime'
import { exportCsv } from '../exportCsv'
import { Download } from '@element-plus/icons-vue'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getLogs, apiErr } from '../api'
const { t } = useI18n()

const logs = ref([])
// 批24 迭代十六：分组筛选 {level:[], module:[]}（级别枚举固定；模块从数据 distinct 预取）
const rowFilter = ref({})
const levelOptions = ['ERROR', 'WARN', 'INFO'].map(v => ({ value: v, label: v }))
const moduleOptions = computed(() => [...new Set(logs.value.map(l => l.module).filter(Boolean))].sort()
  .map(v => ({ value: v, label: v })))
const filterGroups = computed(() => [
  { key: 'level', label: t('log.level'), options: levelOptions },
  { key: 'module', label: t('log.module'), options: moduleOptions.value },
])
const filteredLogs = computed(() => {   // 批25：数据源已切 system_log——email/im/sms 发送事件为真数据行（映射撤）
  const { level = [], module = [] } = rowFilter.value
  return logs.value.filter(l => (!level.length || level.includes(l.level)) && (!module.length || module.includes(l.module)))
})
const onExport = () => exportCsv('run_logs',
  [t('common.time'), t('log.level'), t('log.module'), t('log.content')],
  filteredLogs.value.map(l => [fmtTime.full(l.ts), l.level, l.module, l.msg]))
const noPerm = ref(false)
onMounted(async () => {
  try { logs.value = (await getLogs()).logs || [] }
  // 批25：运行日志=管理员面——403 显式提示非静默空表；批27-19：其余错误（500/网络）也不再静默
  catch (e) {
    if (e?.response?.status === 403) noPerm.value = true
    else ElMessage.error(apiErr(e, t('common.loadFailed')))
  }
})
</script>
