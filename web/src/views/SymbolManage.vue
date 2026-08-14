<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <div>
          <el-button @click="$router.back()" size="small" link>← {{ t('common.return') }}</el-button>
          <span style="margin-left: 8px">{{ t('symbol.listTitle', { title, n: total }) }}</span>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-input v-model="q" :placeholder="t('symbol.phSearch')" size="small" style="width: 180px" clearable @keyup.enter="onSearch" />
          <el-button @click="onSearch" size="small">{{ t('common.search') }}</el-button>
          <el-button @click="load" size="small">{{ t('common.refresh') }}</el-button>
          <el-button type="primary" size="small" @click="onSyncAll" :loading="allRunning">{{ t('symbol.syncAll') }}</el-button>
        </div>
      </div>
    </template>

    <!-- 全量同步进度条 -->
    <el-card v-if="allRunning || progress.status === 'running' || progress.status === 'error'" shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px">
        <el-progress :percentage="Number(progress.pct || 0)" :status="progress.status === 'error' ? 'exception' : ''" style="flex: 1" />
        <span style="font-size: 12px; color: #606266; white-space: nowrap">
          {{ progress.done || 0 }} / {{ progress.total || 0 }} · {{ progress.current || '' }}
          <span v-if="progress.status === 'error'" style="color: #f56c6c">{{ progress.error }}</span>
        </span>
      </div>
    </el-card>

    <!-- 虚拟滚动表格（el-table-v2，5534 只只渲染可见行） -->
    <div style="height: 600px">
      <el-auto-resizer>
        <template #default="{ height, width }">
          <el-table-v2
            :columns="columns"
            :data="items"
            :width="width"
            :height="height"
            :row-height="50"
            v-loading="loading"
            fixed
          />
        </template>
      </el-auto-resizer>
    </div>

    <!-- 回补弹窗 -->
    <el-dialog v-model="bfVisible" :title="t('symbol.backfillTitle', { symbol: bfSymbol })" width="420px">
      <el-form label-width="80px">
        <el-form-item :label="t('symbol.start')">
          <el-input v-model="bfForm.start" :placeholder="t('symbol.phDate')" />
        </el-form-item>
        <el-form-item :label="t('symbol.end')">
          <el-input v-model="bfForm.end" :placeholder="t('symbol.phDate')" />
        </el-form-item>
        <el-alert type="warning" :closable="false" show-icon
          :title="t('symbol.backfillHint')" />
      </el-form>
      <template #footer>
        <el-button @click="bfVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="doBackfill" :loading="bfLoading">{{ t('symbol.backfill') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox, ElButton, ElTag } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const route = useRoute()
const syncId = route.params.syncId
const title = ref(syncId)
const items = ref([])
const total = ref(0)
const q = ref('')
const loading = ref(false)

const allRunning = ref(false)
const progress = ref({})
const taskId = ref('')
const idleSince = ref(0)
let progressTimer = null

// 虚拟滚动 columns（computed 响应语言切换；cellRenderer 用 h 函数）
const columns = computed(() => [
  { key: 'ts_code', dataKey: 'ts_code', title: t('symbol.code'), width: 120 },
  { key: 'name', dataKey: 'name', title: t('common.name'), width: 120 },
  { key: 'list_date', dataKey: 'list_date', title: t('symbol.listDate'), width: 110 },
  {
    key: 'local', title: t('symbol.localData'), width: 240,
    cellRenderer: ({ row }) => row.local_count > 0
      ? h('span', { style: 'font-size: 12px' }, t('symbol.localSummary', { n: row.local_count, first: row.local_first, last: row.local_last }))
      : h(ElTag, { size: 'small', type: 'info' }, () => t('symbol.empty'))
  },
  {
    key: 'actions', title: t('common.action'), width: 280, fixed: 'right',
    cellRenderer: ({ row }) => h('div', { style: 'display: flex; gap: 4px' }, [
      h(ElButton, { size: 'small', type: 'primary', loading: row._loading, onClick: () => onSync(row) }, () => t('symbol.sync')),
      h(ElButton, { size: 'small', type: 'warning', onClick: () => onBackfill(row) }, () => t('symbol.backfill')),
      h(ElButton, { size: 'small', type: 'danger', onClick: () => onDelete(row) }, () => t('common.delete')),
    ])
  },
])

const load = async () => {
  loading.value = true
  try {
    const r = await api.get(`/sync/symbols/${syncId}`, { params: { q: q.value, page: 1, size: 9999 } })
    items.value = r.items
    total.value = r.total
  } finally { loading.value = false }
}
const onSearch = () => load()

const onSync = async (row) => {
  row._loading = true
  try {
    const r = await api.post(`/sync/symbol/${syncId}/${row.ts_code}`, { mode: 'auto' }, { timeout: 120000 })
    if (r.status === 'uptodate') {
      ElMessage.info(t('symbol.uptodate', { code: row.ts_code }))
    } else if (r.status === 'success') {
      ElMessage.success(t('symbol.syncResult', { code: row.ts_code, mode: r.mode_used === 'full' ? t('backtest.modeSingle') : '', pulled: r.pulled, saved: r.saved, range: `${r.range[0]}~${r.range[1]}` }))
    } else {
      ElMessage.warning(t('symbol.syncWarn', { code: row.ts_code, status: r.status, error: r.error || '' }))
    }
    await load()
  } catch (e) { ElMessage.error(e.detail || e.message || t('symbol.syncFailed')) }
  finally { row._loading = false }
}

const bfVisible = ref(false)
const bfSymbol = ref('')
const bfForm = ref({ start: '', end: '' })
const bfLoading = ref(false)
const onBackfill = (row) => {
  bfSymbol.value = row.ts_code
  const today = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const ago = new Date(); ago.setDate(ago.getDate() - 90)
  bfForm.value = { start: ago.toISOString().slice(0, 10).replace(/-/g, ''), end: today }
  bfVisible.value = true
}
const doBackfill = async () => {
  bfLoading.value = true
  try {
    const r = await api.post(`/sync/symbol/${syncId}/${bfSymbol.value}/backfill`,
      { start: bfForm.value.start, end: bfForm.value.end }, { timeout: 120000 })
    if (r.status === 'success') {
      ElMessage.success(t('symbol.backfillResult', { code: bfSymbol.value, pulled: r.pulled, range: `${r.range[0]}~${r.range[1]}` }))
      bfVisible.value = false
      await load()
    } else if (r.status === 'empty') {
      ElMessage.warning(t('symbol.backfillEmpty', { code: bfSymbol.value }))
    } else {
      ElMessage.error(r.error || t('symbol.backfillFailed'))
    }
  } catch (e) { ElMessage.error(e.detail || e.message || t('symbol.backfillFailed')) }
  finally { bfLoading.value = false }
}

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(t('symbol.confirmDelete', { code: row.ts_code }), t('common.confirm'), { type: 'warning' })
    const r = await api.delete(`/sync/symbol/${syncId}/${row.ts_code}`)
    ElMessage.success(t('symbol.deletedRows', { code: row.ts_code, n: r.deleted }))
    await load()
  } catch (e) { if (e !== 'cancel') ElMessage.error(t('common.deleteFailed')) }
}

const onSyncAll = async () => {
  try {
    await ElMessageBox.confirm(t('symbol.confirmSyncAll', { title: title.value }), t('symbol.syncAllTitle'), { type: 'warning' })
    const r = await api.post(`/sync/all/${syncId}`)
    ElMessage.success(t('symbol.taskSubmitted', { id: r.task_id.slice(0, 8) }))
    allRunning.value = true
    startProgress()
  } catch (e) { if (e !== 'cancel') ElMessage.error(t('symbol.submitFailed')) }
}

const startProgress = () => {
  stopProgress()
  progressTimer = setInterval(async () => {
    try {
      const params = taskId.value ? { task_id: taskId.value } : {}
      const p = await api.get(`/sync/all/${syncId}/progress`, { params })
      progress.value = p
      if (p.status === 'running') {
        allRunning.value = true
      } else if (p.status === 'idle') {
        idleSince.value += 2
        if (idleSince.value > 30) {
          allRunning.value = false
          ElMessage.warning(t('symbol.statusQueryFailed'))
          stopProgress()
        }
      } else {
        allRunning.value = false
        if (p.status === 'success' || p.status === 'partial') {
          ElMessage.success(t('symbol.syncAllDone', { ok: p.ok || 0, total: p.total, saved: p.saved || 0, failed: p.failed_count || 0 }))
          stopProgress()
          await load()
        } else if (p.status === 'error') {
          stopProgress()
        }
      }
    } catch { /* ignore */ }
  }, 2000)
}
const stopProgress = () => { if (progressTimer) { clearInterval(progressTimer); progressTimer = null } }

onMounted(async () => {
  await load()
  try {
    const p = await api.get(`/sync/all/${syncId}/progress`)
    progress.value = p
    if (p.status === 'running') { allRunning.value = true; startProgress() }
  } catch (e) { console.error(e) }
})
onUnmounted(stopProgress)
</script>
