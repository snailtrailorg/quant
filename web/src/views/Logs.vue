<template>
  <el-row :gutter="20">
    <el-col :span="14">
      <el-card>
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>{{ t('log.runLogs') }}</span>
            <div style="display: flex; gap: 8px; align-items: center">
              <el-select v-model="levelFilter" size="small" style="width: 100px" :placeholder="t('log.level')" clearable>
                <el-option :label="t('common.all')" value="" />
                <el-option label="ERROR" value="ERROR" />
                <el-option label="WARN" value="WARN" />
                <el-option label="INFO" value="INFO" />
              </el-select>
              <el-button type="primary" size="small" @click="showAnalyze = true" :disabled="!errorLogs.length">{{ t('log.aiAnalyzeCount', { n: errorLogs.length }) }}</el-button>
            </div>
          </div>
        </template>
        <el-table :data="filteredLogs" stripe height="500">
          <el-table-column prop="ts" :label="t('common.time')" width="160">
            <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="level" :label="t('log.level')" width="80">
            <template #default="{ row }">
              <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="module" :label="t('log.module')" width="100" />
          <el-table-column prop="msg" :label="t('log.content')" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="10">
      <el-card>
        <template #header>{{ t('log.alertHistory') }}</template>
        <el-table :data="alerts" stripe height="500">
          <el-table-column prop="level" :label="t('log.level')" width="70">
            <template #default="{ row }">
              <el-tag :type="row.level === 'critical' ? 'danger' : row.level === 'warn' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" :label="t('log.titleCol')" />
          <el-table-column prop="ts" :label="t('common.time')" width="100">
            <template #default="{ row }">{{ (parseFloat(row.ts || 0) * 1000) ? new Date(parseFloat(row.ts) * 1000).toLocaleString() : '-' }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <el-dialog v-model="showAnalyze" :title="t('log.aiTitle')" width="600px">
    <el-alert type="warning" :closable="false" style="margin-bottom: 16px">{{ t('log.analyzeHint', { n: errorLogs.length }) }}</el-alert>
    <el-input v-model="analysisResult" type="textarea" :rows="10" readonly :placeholder="t('log.phAnalyze')" />
    <template #footer>
      <el-button size="small" @click="showAnalyze = false">{{ t('common.close') }}</el-button>
      <el-button size="small" type="primary" @click="doAnalyze" :loading="analyzing">{{ t('log.analyze') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getLogs, getAlerts, logAnalyze } from '../api'
const { t } = useI18n()
const logs = ref([])
const alerts = ref([])
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
  try { alerts.value = (await getAlerts()).alerts || [] } catch {}
})
</script>
