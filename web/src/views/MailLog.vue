<template>
  <div>
    <!-- 批24 迭代十六：邮件发件箱（outbox）从 Logs 拆出独立「邮件日志」页签（用户裁定）；
         无卡模式（Observe 页签卡内）+状态筛选+导出（选项=status distinct 预取） -->
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: var(--sp-3)">
      <RowFilter v-model="statusFilter" :options="statusOptions" :title="t('common.filter')" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExport" />
    </div>
    <TableShell :data="filteredOutbox" max-height="420" storage-key="logs-outbox">
      <el-table-column prop="status" :label="t('common.status')" min-width="100">
        <template #default="{ row }">
          <span style="display:inline-flex; align-items:center; gap:4px"><StatusTag :value="row.status" />{{ row.status === 'pending' ? `(${row.attempts})` : '' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="to" :label="t('log.outboxTo')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="subject" :label="t('log.outboxSubject')" min-width="180" show-overflow-tooltip />
      <el-table-column prop="sent_at" :label="t('cols.sentAt')" min-width="160">
        <template #default="{ row }">{{ row.sent_at ? fmtTime.full(row.sent_at) : '-' }}</template>
      </el-table-column>
      <el-table-column prop="next_attempt_at" :label="t('log.outboxNext')" min-width="160">
        <template #default="{ row }">{{ row.next_attempt_at || '-' }}</template>
      </el-table-column>
      <el-table-column prop="last_error" :label="t('log.outboxError')" min-width="160" show-overflow-tooltip />
    </TableShell>
  </div>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import TableShell from '../components/TableShell.vue'
import RowFilter from '../components/RowFilter.vue'
import IconBtn from '../components/IconBtn.vue'
import { fmtTime } from '../utils/fmtTime'
import { exportCsv } from '../exportCsv'
import { Download } from '@element-plus/icons-vue'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getEmailOutbox } from '../api'
const { t } = useI18n()

const outbox = ref([])
const statusFilter = ref([])
const statusOptions = computed(() => [...new Set(outbox.value.map(o => o.status).filter(Boolean))].sort()
  .map(v => ({ value: v, label: v })))
const filteredOutbox = computed(() => statusFilter.value.length
  ? outbox.value.filter(o => statusFilter.value.includes(o.status)) : outbox.value)
const onExport = () => exportCsv('mail_outbox',
  [t('common.status'), t('log.outboxTo'), t('log.outboxSubject'), t('cols.sentAt'), t('log.outboxNext'), t('log.outboxError')],
  filteredOutbox.value.map(o => [o.status + (o.status === 'pending' ? `(${o.attempts})` : ''), o.to, o.subject, o.sent_at || '-', o.next_attempt_at || '-', o.last_error || '']))
onMounted(async () => { try { outbox.value = (await getEmailOutbox()).items || [] } catch {} })
</script>
