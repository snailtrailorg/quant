<template>
  <div>
    <!-- 批22 追加裁定：通知历史表迁至系统监控页「通知」页签（NotificationTable），本页不再重复 -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('log.runLogs') }}</span>
          <!-- 批23：级别筛选 RowFilter 多选（本地过滤；选项集统一 ERROR/WARNING/INFO/DEBUG） -->
          <div style="display: flex; gap: 8px; align-items: center">
            <RowFilter v-model="levelFilter" :options="levelOptions" :title="t('common.filter')" />
          </div>
        </div>
      </template>
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
      <!-- 批23：表格下方死筛选表单整组删（filterLogs 未定义，批22 前遗留死控件；AI 分析整链同批退役） -->
    </el-card>

  <!-- 邮件发件箱（持久化 + 指数退避重发） -->
  <el-card style="margin-top: 20px">
    <template #header>{{ t('log.outboxTitle') }}</template>
    <TableShell :data="outbox" max-height="300" storage-key="logs-outbox">
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
    <div style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">{{ t('log.outboxHint') }}</div>
  </el-card>
  </div>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import TableShell from '../components/TableShell.vue'
import RowFilter from '../components/RowFilter.vue'
import { fmtTime } from '../utils/fmtTime'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getLogs, getEmailOutbox } from '../api'
const { t } = useI18n()

const logs = ref([])
const outbox = ref([])
// 批23：级别多选本地过滤（[] =全部）；选项集对齐 task_logs 实际枚举（strategy_runner level.upper()）
const levelFilter = ref([])
const levelOptions = ['ERROR', 'WARN', 'INFO'].map(v => ({ value: v, label: v }))
const filteredLogs = computed(() => levelFilter.value.length ? logs.value.filter(l => levelFilter.value.includes(l.level)) : logs.value)
onMounted(() => {
  // 批9：双源各自独立容错→并发发不短路（Dashboard jobs 范式）
  [
    async () => { try { logs.value = (await getLogs()).logs || [] } catch {} },
    async () => { try { outbox.value = (await getEmailOutbox()).items || [] } catch {} },
  ].forEach(fn => fn())
})
</script>

