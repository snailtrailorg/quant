<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>数据同步管理</span>
        <el-button @click="load" size="small">刷新</el-button>
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
      <el-table-column prop="name" label="数据类型" width="160" />
      <el-table-column prop="data_type" label="品类" width="80">
        <template #default="{ row }"><el-tag size="small">{{ row.data_type }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="mode" label="模式" width="80" />
      <el-table-column label="cron 调度" width="200">
        <template #default="{ row }">
          <el-input v-model="row.schedule" size="small" style="width: 170px" placeholder="如 30 16 * * 1-5" @change="onScheduleChange(row)" />
        </template>
      </el-table-column>
      <el-table-column label="交易日过滤" width="120">
        <template #default="{ row }">
          <el-select v-model="row.trade_day_filter" size="small" style="width: 100px" @change="onScheduleChange(row)">
            <el-option label="不过滤" value="none" />
            <el-option label="工作日" value="workday" />
            <el-option label="交易日" value="trade_day" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'running' ? 'warning' : row.status === 'idle' ? 'success' : 'danger'" size="small">
            {{ row.status === 'idle' ? '空闲' : row.status === 'running' ? '运行中' : row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_sync_count" label="上次同步" width="100">
        <template #default="{ row }">{{ row.last_sync_count || 0 }} 条</template>
      </el-table-column>
      <el-table-column prop="last_sync_ts" label="同步时间" width="160">
        <template #default="{ row }">{{ row.last_sync_ts ? row.last_sync_ts.slice(0,16).replace('T',' ') : '-' }}</template>
      </el-table-column>
      <el-table-column label="启用" width="60">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="onToggle(row)" size="small" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="290">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="onTrigger(row)" :loading="row.status === 'running'">同步</el-button>
          <el-button size="small" type="warning" @click="onBackfill(row)" v-if="row.mode === 'incremental'">回补</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)" v-if="role === 'admin'">删除</el-button>
          <el-button size="small" @click="goSymbols(row)" v-if="isPerSymbol(row.id)">管理标的</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-divider />
    <el-card>
      <template #header>同步日志</template>
      <el-table :data="logs" stripe max-height="300">
        <el-table-column prop="sync_id" label="任务" width="120" />
        <el-table-column prop="ts" label="时间" width="160">
          <template #default="{ row }">{{ row.ts.slice(0,16).replace('T',' ') }}</template>
        </el-table-column>
        <el-table-column prop="mode" label="模式" width="80" />
        <el-table-column prop="rows_pulled" label="拉取" width="80" />
        <el-table-column prop="rows_saved" label="入库" width="80" />
        <el-table-column prop="duration_ms" label="耗时" width="80">
          <template #default="{ row }">{{ row.duration_ms }}ms</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'success' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="交易日" width="100">
          <template #default="{ row }">
            <span v-if="row.expected_days != null">{{ row.actual_days }}/{{ row.expected_days }}</span>
            <span v-else style="color:#999">-</span>
          </template>
        </el-table-column>
        <el-table-column label="缺口" min-width="180">
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import api from '../api'

const router = useRouter()
const configs = ref([])
const logs = ref([])
const loading = ref(false)
const currentSync = ref(null)  // 当前异步同步任务 {sid, name, task_id}
const progress = ref({})
let pollTimer = null
const role = ref(localStorage.getItem('role') || 'viewer')

const PER_SYMBOL_IDS = ['astock_daily', 'etf_daily', 'cb_daily']
const isPerSymbol = id => PER_SYMBOL_IDS.includes(id)
const goSymbols = row => router.push(`/data-manage/${row.id}`)

const load = async () => {
  loading.value = true
  try {
    configs.value = await api.get('/sync/config')
    logs.value = await api.get('/sync/log')
  } finally { loading.value = false }
}
const onScheduleChange = async (row) => {
  await api.put(`/sync/config/${row.id}`, { schedule: row.schedule, enabled: row.enabled, trade_day_filter: row.trade_day_filter })
  ElMessage.success(`${row.name} 调度已更新`)
}
const onToggle = async (row) => {
  await api.put(`/sync/config/${row.id}`, { schedule: row.schedule, enabled: row.enabled })
  ElMessage.success(`${row.name} ${row.enabled ? '已启用' : '已停用'}`)
}

// 异步同步完成提示（适配轮询 progress 结果，用 failed_dates_count）
const notifyResult = (row, p, prefix) => {
  if (p.status === 'error') { ElMessage.error(`${prefix} 失败: ${p.error || ''}`); return }
  let msg = `${prefix}: 拉取${p.rows_pulled || 0} 入库${p.rows_saved || 0}`
  if (p.expected_days) msg += ` (${p.actual_days}/${p.expected_days}交易日)`
  if (p.failed_dates_count) msg += ` 缺口${p.failed_dates_count}天`
  ElMessage[p.failed_dates_count ? 'warning' : 'success'](msg)
}

// 轮询类型级同步进度（异步化后 HTTP 立即返回 task_id，前端轮询 /sync/trigger/{sid}/progress）
const startPoll = (row, name, task_id) => {
  stopPoll()
  currentSync.value = { sid: row.id, name, task_id }
  let idleTicks = 0
  pollTimer = setInterval(async () => {
    try {
      const p = await api.get(`/sync/trigger/${row.id}/progress`, { params: { task_id } })
      progress.value = p
      if (p.status === 'running') {
        row.status = 'running'
        idleTicks = 0
      } else if (p.status === 'idle') {
        // hash 过期且 Celery 未就绪（worker 未起？）-- 累计等待，超 30s 放弃
        if (++idleTicks > 15) {
          stopPoll()
          ElMessage.warning(`${name}: 任务状态查询超时（worker 可能未运行）`)
          currentSync.value = null; progress.value = {}; row.status = 'idle'
        }
      } else {
        // success/partial/error 完成
        stopPoll()
        notifyResult(row, p, name)
        currentSync.value = null; progress.value = {}
        row.status = 'idle'
        await load()
      }
    } catch (e) { /* ignore 单次轮询失败 */ }
  }, 2000)
}
const stopPoll = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

const onTrigger = async (row) => {
  row.status = 'running'
  try {
    const r = await api.post(`/sync/trigger/${row.id}`)
    if (r.status === 'submitted') {
      startPoll(row, `${row.name} 同步`, r.task_id)
    } else {
      notifyResult(row, r, `${row.name} 同步`)
      row.status = 'idle'
    }
  } catch (e) { ElMessage.error('提交失败'); row.status = 'idle' }
}

const onBackfill = async (row) => {
  // 默认起始日期：30 天前
  const d = new Date(); d.setDate(d.getDate() - 30)
  const def = d.toISOString().slice(0, 10).replace(/-/g, '')
  try {
    const { value } = await ElMessageBox.prompt('起始日期 YYYYMMDD（如 20260626）', `回补 ${row.name}`, {
      inputValue: def,
      inputPattern: /^\d{8}$/,
      inputErrorMessage: '日期格式须为 YYYYMMDD',
      confirmButtonText: '回补',
      cancelButtonText: '取消',
      type: 'warning',
    })
    row.status = 'running'
    try {
      const r = await api.post(`/sync/trigger/${row.id}`, null, { params: { backfill_from: value } })
      if (r.status === 'submitted') {
        startPoll(row, `${row.name} 回补 ${value}`, r.task_id)
      } else {
        notifyResult(row, r, `${row.name} 回补 ${value}`)
        row.status = 'idle'
      }
    } catch (e) { ElMessage.error('提交失败'); row.status = 'idle' }
  } catch (e) {
    // 用户取消 prompt，不动状态
  }
}

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.name} 的全部数据？此操作不可恢复`, '高危确认', { type: 'warning' })
    await api.delete(`/sync/data/${row.id}`)
    ElMessage.success('数据已删除')
    await load()
  } catch {}
}
onMounted(load)
onUnmounted(stopPoll)
</script>
