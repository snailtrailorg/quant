<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <div>
          <el-button @click="$router.back()" size="small" link>← 返回</el-button>
          <span style="margin-left: 8px">{{ title }} · 标的列表（{{ total }} 只，虚拟滚动）</span>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-input v-model="q" placeholder="搜代码/名称" size="small" style="width: 180px" clearable @keyup.enter="onSearch" />
          <el-button @click="onSearch" size="small">搜索</el-button>
          <el-button @click="load" size="small">刷新</el-button>
          <el-button type="primary" size="small" @click="onSyncAll" :loading="allRunning">全量同步全部</el-button>
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
    <el-dialog v-model="bfVisible" :title="`回补 ${bfSymbol}`" width="420px">
      <el-form label-width="80px">
        <el-form-item label="起始">
          <el-input v-model="bfForm.start" placeholder="YYYYMMDD 如 20260626" />
        </el-form-item>
        <el-form-item label="结束">
          <el-input v-model="bfForm.end" placeholder="YYYYMMDD 如 20260726" />
        </el-form-item>
        <el-alert type="warning" :closable="false" show-icon
          title="回补会覆盖本地已有数据（手动回补优先级高于增量）" />
      </el-form>
      <template #footer>
        <el-button @click="bfVisible = false">取消</el-button>
        <el-button type="primary" @click="doBackfill" :loading="bfLoading">回补</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted, h } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, ElButton, ElTag } from 'element-plus'
import api from '../api'

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

// 虚拟滚动 columns（cellRenderer 用 h 函数）
const columns = [
  { key: 'ts_code', dataKey: 'ts_code', title: '代码', width: 120 },
  { key: 'name', dataKey: 'name', title: '名称', width: 120 },
  { key: 'list_date', dataKey: 'list_date', title: '上市日', width: 110 },
  {
    key: 'local', title: '本地数据', width: 240,
    cellRenderer: ({ row }) => row.local_count > 0
      ? h('span', { style: 'font-size: 12px' }, `${row.local_count} 根 · ${row.local_first} ~ ${row.local_last}`)
      : h(ElTag, { size: 'small', type: 'info' }, () => '空')
  },
  {
    key: 'actions', title: '操作', width: 280, fixed: 'right',
    cellRenderer: ({ row }) => h('div', { style: 'display: flex; gap: 4px' }, [
      h(ElButton, { size: 'small', type: 'primary', loading: row._loading, onClick: () => onSync(row) }, () => '同步'),
      h(ElButton, { size: 'small', type: 'warning', onClick: () => onBackfill(row) }, () => '回补'),
      h(ElButton, { size: 'small', type: 'danger', onClick: () => onDelete(row) }, () => '删除'),
    ])
  },
]

// 一次加载全部（size=9999），el-table-v2 虚拟滚动只渲染可见行
const load = async () => {
  loading.value = true
  try {
    const r = await api.get(`/sync/symbols/${syncId}`, { params: { q: q.value, page: 1, size: 9999 } })
    items.value = r.items
    total.value = r.total
  } finally { loading.value = false }
}
const onSearch = () => load()

// 单只同步
const onSync = async (row) => {
  row._loading = true
  try {
    const r = await api.post(`/sync/symbol/${syncId}/${row.ts_code}`, { mode: 'auto' }, { timeout: 120000 })
    if (r.status === 'uptodate') {
      ElMessage.info(`${row.ts_code} 已是最新`)
    } else if (r.status === 'success') {
      ElMessage.success(`${row.ts_code} ${r.mode_used === 'full' ? '全量' : '增量'}拉取${r.pulled} 入库${r.saved} (${r.range[0]}~${r.range[1]})`)
    } else {
      ElMessage.warning(`${row.ts_code} ${r.status}: ${r.error || ''}`)
    }
    await load()
  } catch (e) { ElMessage.error(e.detail || e.message || '同步失败') }
  finally { row._loading = false }
}

// 回补
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
      ElMessage.success(`${bfSymbol.value} 回补${r.pulled}条 覆盖入库 (${r.range[0]}~${r.range[1]})`)
      bfVisible.value = false
      await load()
    } else if (r.status === 'empty') {
      ElMessage.warning(`${bfSymbol.value} 该区间无数据`)
    } else {
      ElMessage.error(r.error || '回补失败')
    }
  } catch (e) { ElMessage.error(e.detail || e.message || '回补失败') }
  finally { bfLoading.value = false }
}

// 删除
const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(`确认删除 ${row.ts_code} 的本地数据？再次同步即完整重建`, '确认', { type: 'warning' })
    const r = await api.delete(`/sync/symbol/${syncId}/${row.ts_code}`)
    ElMessage.success(`${row.ts_code} 已删除 ${r.deleted} 行`)
    await load()
  } catch (e) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

// 全量同步（Celery 后台）
const onSyncAll = async () => {
  try {
    await ElMessageBox.confirm(`全量同步 ${title.value} 全部标的？后台执行，约数分钟到数十分钟。`, '全量同步', { type: 'warning' })
    const r = await api.post(`/sync/all/${syncId}`)
    ElMessage.success(`已提交后台任务 ${r.task_id.slice(0, 8)}`)
    allRunning.value = true
    startProgress()
  } catch (e) { if (e !== 'cancel') ElMessage.error('提交失败') }
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
          ElMessage.warning('任务状态查询失败（可能未启动或 worker 未运行）')
          stopProgress()
        }
      } else {
        allRunning.value = false
        if (p.status === 'success' || p.status === 'partial') {
          ElMessage.success(`全量完成: ${p.ok || 0}/${p.total} 成功, 入库 ${p.saved || 0}, 失败 ${p.failed_count || 0}`)
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
