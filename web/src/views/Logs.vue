<template>
  <div>
    <!-- 批24 迭代十六 hotfix4（用户裁定：条目级整合非页面拼接）：邮件发送记录映射为日志条目混排进本表
         （level=status 映射/module=email/msg=收件人+主题+错误），双源合并按时间倒序；
         分组筛选（级别+模块——模块 distinct 预取自然含 email）+导出（合并行） -->
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: var(--sp-3)">
      <RowFilter v-model="rowFilter" :groups="filterGroups" :title="t('common.filter')" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExport" />
    </div>
    <TableShell :data="filteredLogs" height="500" storage-key="logs-run">
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
import { getLogs, getEmailOutbox } from '../api'
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
// 邮件发送记录→日志条目映射（hotfix4 条目级整合）：level 按发送状态、module 固定 email
const mergedLogs = computed(() => [
  ...logs.value,
  ...outbox.value.map(o => ({
    ts: o.sent_at || o.created_at,
    level: o.status === 'failed' ? 'ERROR' : o.status === 'pending' ? 'WARN' : 'INFO',
    module: 'email',
    msg: `→ ${o.to} ｜ ${o.subject}` + (o.status === 'pending' ? `（重试 ${o.attempts} 次）` : '') + (o.last_error ? `（${o.last_error}）` : ''),
  })),
].sort((a, b) => (b.ts || '').localeCompare(a.ts || '')))
const filteredLogs = computed(() => {
  const { level = [], module = [] } = rowFilter.value
  return mergedLogs.value.filter(l => (!level.length || level.includes(l.level)) && (!module.length || module.includes(l.module)))
})
const onExport = () => exportCsv('run_logs',
  [t('common.time'), t('log.level'), t('log.module'), t('log.content')],
  filteredLogs.value.map(l => [fmtTime.full(l.ts), l.level, l.module, l.msg]))
const outbox = ref([])   // 邮件发送记录源（hotfix4：映射进 mergedLogs 混排，不再独立表）
onMounted(() => {
  [async () => { try { logs.value = (await getLogs()).logs || [] } catch {} },
   async () => { try { outbox.value = (await getEmailOutbox()).items || [] } catch {} }].forEach(fn => fn())   // 批9 双源独立容错
})
</script>
