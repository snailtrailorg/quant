<template>
  <div>
    <!-- 批22 追加裁定：通知历史表迁至系统监控页「通知」页签（NotificationTable），本页不再重复 -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('log.runLogs') }}</span>
          <div style="display: flex; gap: 8px; align-items: center">
            <el-select v-model="levelFilter" style="width: 100px" :placeholder="t('log.level')" clearable>
              <el-option :label="t('common.all')" value="" />
              <el-option label="ERROR" value="ERROR" />
              <el-option label="WARN" value="WARN" />
              <el-option label="INFO" value="INFO" />
            </el-select>
            <el-button type="primary" @click="showAnalyze = true" :disabled="!errorLogs.length">{{ t('log.aiAnalyzeCount', { n: errorLogs.length }) }}</el-button>
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
      <!-- P3-5(05 §5.10):日志筛选 -->
      <el-form inline style="margin-top: var(--sp-2)">
        <el-form-item><el-input v-model="logKw" :placeholder="t('log.keyword')" clearable style="width: 200px" @change="filterLogs" /></el-form-item>
        <el-form-item>
          <el-select v-model="logLevel" :placeholder="t('log.level')" clearable style="width: 100px" @change="filterLogs">
            <el-option v-for="lv in ['ERROR','WARNING','INFO','DEBUG']" :key="lv" :value="lv" :label="lv" />
          </el-select>
        </el-form-item>
        <el-form-item><el-date-picker v-model="logRange" type="datetimerange" style="width: 280px" @change="filterLogs" /></el-form-item>
      </el-form>
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

  <el-dialog v-model="showAnalyze" :title="t('log.aiTitle')" width="720px">
    <el-alert type="warning" :closable="false" style="margin-bottom: var(--sp-4)">{{ t('log.analyzeHint', { n: errorLogs.length }) }}</el-alert>
    <el-input v-model="analysisResult" type="textarea" :rows="10" readonly :placeholder="t('log.phAnalyze')" />
    <template #footer>
      <el-button type="primary" @click="showAnalyze = false">{{ t('common.close') }}</el-button>
      <el-button type="primary" @click="doAnalyze" :loading="analyzing">{{ t('log.analyze') }}</el-button>
    </template>
  </el-dialog>
  </div>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import TableShell from '../components/TableShell.vue'
import { fmtTime } from '../utils/fmtTime'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getLogs, logAnalyze, getEmailOutbox } from '../api'
const { t } = useI18n()

const logs = ref([])
const outbox = ref([])
const showAnalyze = ref(false)
const analyzing = ref(false)
const analysisResult = ref('')
const levelFilter = ref('')
const filteredLogs = computed(() => {
  if (!levelFilter.value) return logs.value
  return logs.value.filter(l => l.level === levelFilter.value)
})
const errorLogs = computed(() => (logs.value || []).filter(l => l.level === 'ERROR' || l.level === 'WARN'))
const doAnalyze = async () => {
  analyzing.value = true; analysisResult.value = ''
  try {
    const r = await logAnalyze({ logs: errorLogs.value })
    analysisResult.value = r.analysis || t('log.noResult')
  } catch (e) { ElMessage.error(t('log.analyzeFailed')) }
  finally { analyzing.value = false }
}
onMounted(() => {
  // 批9：双源各自独立容错→并发发不短路（Dashboard jobs 范式）
  [
    async () => { try { logs.value = (await getLogs()).logs || [] } catch {} },
    async () => { try { outbox.value = (await getEmailOutbox()).items || [] } catch {} },
  ].forEach(fn => fn())
})

// P3-5:日志筛选
const logKw = ref('')
const logLevel = ref('')
const logRange = ref(null)
// P3-5:日志筛选

</script>

