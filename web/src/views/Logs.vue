<template>
  <el-row :gutter="20">
    <el-col :span="14">
      <el-card>
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>运行日志</span>
            <div style="display: flex; gap: 8px; align-items: center">
              <el-select v-model="levelFilter" size="small" style="width: 100px" placeholder="级别" clearable>
                <el-option label="全部" value="" />
                <el-option label="ERROR" value="ERROR" />
                <el-option label="WARN" value="WARN" />
                <el-option label="INFO" value="INFO" />
              </el-select>
              <el-button type="primary" size="small" @click="showAnalyze = true" :disabled="!errorLogs.length">AI 归因（{{ errorLogs.length }} 条异常）</el-button>
            </div>
          </div>
        </template>
        <el-table :data="filteredLogs" stripe height="500">
          <el-table-column prop="ts" label="时间" width="160">
            <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="level" label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="module" label="模块" width="100" />
          <el-table-column prop="msg" label="内容" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="10">
      <el-card>
        <template #header>告警历史</template>
        <el-table :data="alerts" stripe height="500">
          <el-table-column prop="level" label="级别" width="70">
            <template #default="{ row }">
              <el-tag :type="row.level === 'critical' ? 'danger' : row.level === 'warn' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="标题" />
          <el-table-column prop="ts" label="时间" width="100">
            <template #default="{ row }">{{ (parseFloat(row.ts || 0) * 1000) ? new Date(parseFloat(row.ts) * 1000).toLocaleString() : '-' }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <el-dialog v-model="showAnalyze" title="AI 日志归因" width="600px">
    <el-alert type="warning" :closable="false" style="margin-bottom: 16px">对 {{ errorLogs.length }} 条 ERROR/WARN 日志进行 AI 归因分析。</el-alert>
    <el-input v-model="analysisResult" type="textarea" :rows="10" readonly placeholder="点击分析按钮..." />
    <template #footer>
      <el-button @click="showAnalyze = false">关闭</el-button>
      <el-button type="primary" @click="doAnalyze" :loading="analyzing">分析</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getLogs, getAlerts, logAnalyze } from '../api'
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
    analysisResult.value = r.analysis || '无分析结果'
  } catch (e) { ElMessage.error('归因失败') }
  finally { analyzing.value = false }
}
onMounted(async () => {
  try { logs.value = (await getLogs()).logs || [] } catch {}
  try { alerts.value = (await getAlerts()).alerts || [] } catch {}
})
</script>