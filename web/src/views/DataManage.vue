<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>数据同步管理</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import api from '../api'

const router = useRouter()
const configs = ref([])
const logs = ref([])
const loading = ref(false)
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

// 结果提示：含缺口时黄色警告
const notifyResult = (row, r, prefix) => {
  let msg = `${prefix}: 拉取${r.rows_pulled || 0} 入库${r.rows_saved || 0}`
  if (r.expected_days != null) msg += ` (${r.actual_days}/${r.expected_days}交易日)`
  if (r.failed_dates?.length) msg += ` 缺口${r.failed_dates.length}天`
  ElMessage[r.failed_dates?.length ? 'warning' : 'success'](msg)
}

const onTrigger = async (row) => {
  row.status = 'running'
  try {
    const r = await api.post(`/sync/trigger/${row.id}`)
    if (r.status === 'rebuild_submitted') {
      // 空状态自动全量重建，提示去二级页看进度
      ElMessageBox.confirm(
        `${row.name} 数据为空，已提交全量重建（后台执行）。\n是否前往标的列表查看进度？`,
        '全量重建已提交', { confirmButtonText: '查看进度', cancelButtonText: '稍后', type: 'success' }
      ).then(() => goSymbols(row)).catch(() => {})
    } else {
      notifyResult(row, r, `${row.name} 同步`)
    }
    await load()
  } catch (e) { ElMessage.error('同步失败'); row.status = 'idle' }
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
      notifyResult(row, r, `${row.name} 回补 ${value}`)
      await load()
    } catch (e) { ElMessage.error('回补失败'); row.status = 'idle' }
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
</script>
