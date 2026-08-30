<template>
  <el-row :gutter="20">
    <el-col :span="14">
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
        <el-table :data="filteredLogs" height="500">
          <el-table-column prop="ts" :label="t('common.time')" width="160">
            <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="level" :label="t('log.level')" width="80">
            <template #default="{ row }">
              <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="module" :label="t('log.module')" width="100" />
          <el-table-column prop="msg" :label="t('log.content')" />
        </el-table>
        <!-- P3-5(05 §5.10):日志筛选 -->
  <el-card style="margin-bottom: 14px">
    <el-form inline>
      <el-form-item><el-input v-model="logKw" :placeholder="t('logs.keyword')" clearable style="width: 200px" @change="filterLogs" /></el-form-item>
      <el-form-item>
        <el-select v-model="logLevel" :placeholder="t('logs.level')" clearable style="width: 100px" @change="filterLogs">
          <el-option v-for="lv in ['ERROR','WARNING','INFO','DEBUG']" :key="lv" :value="lv" :label="lv" />
        </el-select>
      </el-form-item>
      <el-form-item><el-date-picker v-model="logRange" type="datetimerange" style="width: 280px" @change="filterLogs" /></el-form-item>
    </el-form>
  </el-card>
</el-card>
    </el-col>
    <el-col :span="10">
      <el-card>
        <template #header>{{ t('log.notifyHistory') }}</template>
        <el-table :data="notifs" height="500">
          <el-table-column prop="level" :label="t('log.level')" width="90">
            <template #default="{ row }">
              <span :class="['ndot', row.level]"></span>{{ row.level }}
            </template>
          </el-table-column>
          <el-table-column prop="category" :label="t('log.notifyCategory')" width="80" />
          <el-table-column prop="title" :label="t('log.titleCol')" width="150" />
          <el-table-column prop="body" :label="t('log.content')" min-width="220" show-overflow-tooltip />
          <el-table-column prop="created_at" :label="t('common.time')" width="150" />
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <!-- 邮件发件箱（持久化 + 指数退避重发） -->
  <el-card style="margin-top: 20px">
    <template #header>{{ t('log.outboxTitle') }}</template>
    <el-table :data="outbox" max-height="300">
      <el-table-column prop="status" :label="t('common.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'sent' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'">
            {{ row.status }}{{ row.status === 'pending' ? ` (${row.attempts})` : '' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="to" :label="t('log.outboxTo')" width="200" show-overflow-tooltip />
      <el-table-column prop="subject" :label="t('log.outboxSubject')" min-width="180" show-overflow-tooltip />
      <el-table-column prop="next_attempt_at" :label="t('log.outboxNext')" width="160">
        <template #default="{ row }">{{ row.next_attempt_at || '-' }}</template>
      </el-table-column>
      <el-table-column prop="last_error" :label="t('log.outboxError')" min-width="160" show-overflow-tooltip />
    </el-table>
    <div style="color: #909399; font-size: 12px; margin-top: 8px">{{ t('log.outboxHint') }}</div>
  </el-card>

  <el-dialog v-model="showAnalyze" :title="t('log.aiTitle')" width="600px">
    <el-alert type="warning" :closable="false" style="margin-bottom: 16px">{{ t('log.analyzeHint', { n: errorLogs.length }) }}</el-alert>
    <el-input v-model="analysisResult" type="textarea" :rows="10" readonly :placeholder="t('log.phAnalyze')" />
    <template #footer>
      <el-button type="primary" @click="showAnalyze = false">{{ t('common.close') }}</el-button>
      <el-button type="primary" @click="doAnalyze" :loading="analyzing">{{ t('log.analyze') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getLogs, logAnalyze, getEmailOutbox, getNotifications } from '../api'
const { t } = useI18n()
const logs = ref([])
const notifs = ref([])
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
onMounted(async () => {
  try { logs.value = (await getLogs()).logs || [] } catch {}
  try { notifs.value = (await getNotifications('all', 50)).items || [] } catch {}
  try { outbox.value = (await getEmailOutbox()).items || [] } catch {}
})

// P3-5:日志筛选
const logKw = ref('')
const logLevel = ref('')
const logRange = ref(null)
// P3-5:日志筛选

</script>
<style scoped>
.ndot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.ndot.critical { background: #f56c6c; }
.ndot.warn { background: #e6a23c; }
.ndot.info { background: #909399; }
</style>

