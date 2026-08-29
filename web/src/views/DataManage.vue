<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('dataManage.title') }}</span>
        <el-button type="primary" @click="load">{{ t('common.refresh') }}</el-button>
      </div>
    </template>
    <el-card v-if="currentSync" shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px">
        <span style="font-size: 13px; white-space: nowrap">{{ currentSync.name }}</span>
        <el-progress :percentage="Number(progress.pct || 0)" :status="progress.status === 'error' ? 'exception' : ''" style="flex: 1" />
        <span style="font-size: 12px; color: #606266; white-space: nowrap">
          {{ progress.done || 0 }} / {{ progress.total || 0 }} · {{ progress.current || '' }}
          <span v-if="progress.status === 'error'" style="color: #f56c6c">{{ progress.error }}</span>
        </span>
      </div>
    </el-card>
    <el-table :data="configs" stripe v-loading="loading">
      <el-table-column prop="name" :label="t('dataManage.dataType')" width="160" />
      <el-table-column prop="data_type" :label="t('dataManage.category')" width="80">
        <template #default="{ row }"><el-tag>{{ row.data_type }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="mode" :label="t('common.mode')" width="80" />
      <el-table-column :label="t('dataManage.cronSchedule')" width="200">
        <template #default="{ row }">
          <el-input v-model="row.schedule" style="width: 170px" :placeholder="t('dataManage.phCron')" @change="onScheduleChange(row)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('dataManage.tradeDayFilter')" width="120">
        <template #default="{ row }">
          <el-select v-model="row.trade_day_filter" style="width: 100px" @change="onScheduleChange(row)">
            <el-option :label="t('dataManage.filterNone')" value="none" />
            <el-option :label="t('dataManage.filterWorkday')" value="workday" />
            <el-option :label="t('dataManage.filterTradeDay')" value="trade_day" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.status')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'idle' ? 'success' : (row.status === 'running' || row.status === 'partial') ? 'warning' : 'danger'">
            {{ row.status === 'idle' ? t('dataManage.idle') : row.status === 'running' ? t('task.statusRunning') : row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_sync_count" :label="t('dataManage.lastSync')" width="100">
        <template #default="{ row }">{{ t('dataManage.rowsCount', { n: row.last_sync_count || 0 }) }}</template>
      </el-table-column>
      <el-table-column prop="last_sync_ts" :label="t('dataManage.syncTime')" width="160">
        <template #default="{ row }">{{ row.last_sync_ts ? row.last_sync_ts.slice(0,16).replace('T',' ') : '-' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.enable')" width="60">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="onToggle(row)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="290">
        <template #default="{ row }">
          <el-button type="primary" @click="onTrigger(row)" :loading="row.status === 'running'">{{ t('dataManage.syncBtn') }}</el-button>
          <el-button type="warning" @click="onBackfill(row)" v-if="row.mode === 'incremental'">{{ t('symbol.backfill') }}</el-button>
          <el-button type="danger" @click="onDelete(row)" v-if="role === 'admin'">{{ t('common.delete') }}</el-button>
          <el-button type="primary" @click="goSymbols(row)" v-if="isPerSymbol(row.id)">{{ t('dataManage.manageSymbols') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-divider />
    <el-card>
      <template #header>{{ t('dataManage.syncLogs') }}</template>
      <el-table :data="logs" stripe max-height="300">
        <el-table-column prop="sync_id" :label="t('dataManage.task')" width="120" />
        <el-table-column prop="ts" :label="t('common.time')" width="160">
          <template #default="{ row }">{{ row.ts.slice(0,16).replace('T',' ') }}</template>
        </el-table-column>
        <el-table-column prop="mode" :label="t('common.mode')" width="80" />
        <el-table-column prop="rows_pulled" :label="t('dataManage.pulled')" width="80" />
        <el-table-column prop="rows_saved" :label="t('dataManage.saved')" width="80" />
        <el-table-column prop="duration_ms" :label="t('dataManage.duration')" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column prop="status" :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('dataManage.tradeDay')" width="100">
          <template #default="{ row }">
            <span v-if="row.expected_days != null">{{ row.actual_days }}/{{ row.expected_days }}</span>
            <span v-else style="color:#999">-</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('dataManage.gap')" min-width="180">
          <template #default="{ row }">
            <span v-if="row.failed_dates" style="color:#f56c6c; font-size:12px">{{ row.failed_dates }}</span>
            <span v-else style="color:#999">-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import api from '../api'

const { t } = useI18n()
const router = useRouter()
const configs = ref([])
const logs = ref([])
const loading = ref(false)
const currentSync = ref(null)  // 当前异步同步任务 {sid, name, task_id}
const progress = ref({})
let pollTimer = null
const role = ref(localStorage.getItem('role') || 'viewer')
const _pollingActive = ref(false)
const setRowStatus = (id, status) => { configs.value = configs.value.map(c => c.id === id ? { ...c, status } : c) }

const PER_SYMBOL_IDS = ['astock_daily', 'etf_daily', 'cb_daily', 'astock_minute', 'astock_minute_5min']
const isPerSymbol = id => PER_SYMBOL_IDS.includes(id)
const goSymbols = row => router.push(`/data-manage/${row.id}`)

const load = async () => {
  loading.value = true
  try {
    configs.value = (await api.get('/sync/config')).map(c => ({ ...c, status: c.last_status ?? 'idle', _prevSchedule: c.schedule, _prevFilter: c.trade_day_filter }))
    logs.value = await api.get('/sync/log')
  } finally { loading.value = false }
}
const onScheduleChange = async (row) => {
  // H10（01 §3.2）：表内裸输入不再直写库——confirm+取消回滚旧值（弹窗化编辑留 P3-4）
  const prev = { schedule: row._prevSchedule ?? row.schedule, filter: row._prevFilter ?? row.trade_day_filter }
  try {
    await ElMessageBox.confirm(
      t('dataManage.confirmSchedule', { name: row.name, cron: row.schedule }), t('common.confirm'), { type: 'warning' })
  } catch {
    row.schedule = prev.schedule; row.trade_day_filter = prev.filter   // 回滚,表内值还原
    return
  }
  try {
    await api.post(`/sync/config/${row.id}`, { schedule: row.schedule, enabled: row.enabled, trade_day_filter: row.trade_day_filter })
    row._prevSchedule = row.schedule; row._prevFilter = row.trade_day_filter
    ElMessage.success(t('dataManage.scheduleUpdated', { name: row.name }))
  } catch (e) {
    row.schedule = prev.schedule; row.trade_day_filter = prev.filter
    ElMessage.error(t('dataManage.scheduleUpdateFailed'))
  }
}
const onToggle = async (row) => {
  await api.post(`/sync/config/${row.id}`, { schedule: row.schedule, enabled: row.enabled })
  ElMessage.success(`${row.name} ${row.enabled ? t('common.enabled') : t('common.disabled')}`)
}

// 异步同步完成提示（适配轮询 progress 结果，用 failed_dates_count）
const notifyResult = (row, p, prefix) => {
  if (p.status === 'error') { ElMessage.error(t('dataManage.syncFailedMsg', { name: prefix, error: p.error || '' })); return }
  let msg = t('dataManage.syncResultMsg', { name: prefix, pulled: p.rows_pulled || 0, saved: p.rows_saved || 0 })
  if (p.expected_days) msg += t('dataManage.tradeDayPart', { actual: p.actual_days, expected: p.expected_days })
  if (p.failed_dates_count) msg += t('dataManage.gapPart', { n: p.failed_dates_count })
  ElMessage[p.failed_dates_count ? 'warning' : 'success'](msg)
}

// 轮询类型级同步进度（异步化后 HTTP 立即返回 task_id，前端轮询 /sync/trigger/{sid}/progress）
const startPoll = (row, name, task_id) => {
  stopPoll()
  _pollingActive.value = true
  currentSync.value = { sid: row.id, name, task_id }
  let idleTicks = 0
  pollTimer = setInterval(async () => {
    try {
      const p = await api.get(`/sync/trigger/${row.id}/progress`, { params: { task_id } })
      progress.value = p
      if (p.status === 'running') {
        setRowStatus(row.id, 'running')
        idleTicks = 0
      } else if (p.status === 'idle') {
        if (++idleTicks > 15) {
          stopPoll()
          ElMessage.warning(t('dataManage.pollTimeout', { name }))
          currentSync.value = null; progress.value = {}; setRowStatus(row.id, 'idle')
        }
      } else {
        stopPoll()
        notifyResult(row, p, name)
        currentSync.value = null; progress.value = {}
        setRowStatus(row.id, 'idle')
        await load()
      }
    } catch (e) { /* ignore 单次轮询失败 */ }
  }, 2000)
}
const stopPoll = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null }; _pollingActive.value = false }

const onTrigger = async (row) => {
  if (_pollingActive.value) return
  setRowStatus(row.id, 'running')
  try {
    const r = await api.post(`/sync/trigger/${row.id}`)
    if (r.status === 'submitted') {
      startPoll(row, t('dataManage.syncTaskName', { name: row.name }), r.task_id)
    } else {
      notifyResult(row, r, t('dataManage.syncTaskName', { name: row.name }))
      setRowStatus(row.id, 'idle')
    }
  } catch (e) { ElMessage.error(t('dataManage.submitFailed')); setRowStatus(row.id, 'idle') }
}

const onBackfill = async (row) => {
  // 默认起始日期：30 天前
  const d = new Date(); d.setDate(d.getDate() - 30)
  const def = d.toISOString().slice(0, 10).replace(/-/g, '')
  try {
    const { value } = await ElMessageBox.prompt(t('dataManage.backfillPrompt'), t('dataManage.backfillTitle', { name: row.name }), {
      inputValue: def,
      inputPattern: /^\d{8}$/,
      inputErrorMessage: t('dataManage.dateFormatError'),
      confirmButtonText: t('symbol.backfill'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
    setRowStatus(row.id, 'running')
    try {
      const r = await api.post(`/sync/trigger/${row.id}`, null, { params: { backfill_from: value } })
      if (r.status === 'submitted') {
        startPoll(row, t('dataManage.backfillTaskName', { name: row.name, value }), r.task_id)
      } else {
        notifyResult(row, r, t('dataManage.backfillTaskName', { name: row.name, value }))
        setRowStatus(row.id, 'idle')
      }
    } catch (e) { ElMessage.error(t('dataManage.submitFailed')); setRowStatus(row.id, 'idle') }
  } catch (e) {
    // 用户取消 prompt，不动状态
  }
}

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(t('dataManage.confirmDeleteAll', { name: row.name }), t('task.highRiskConfirm'), { type: 'warning' })
    await api.delete(`/sync/data/${row.id}`)
    ElMessage.success(t('dataManage.dataDeleted'))
    await load()
  } catch {}
}
onMounted(load)
onUnmounted(stopPoll)
</script>
