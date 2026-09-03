<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('liveTask.title') }}</span>
        <el-button type="primary" @click="openCreate">{{ t('liveTask.create') }}</el-button>
      </div>
    </template>
    <el-table :data="tasks">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
      <el-table-column prop="strategy_id" :label="t('liveTask.strategy')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip />
      <!-- P1-5（06 B#5）：md_mode/行情 lag/bars 消费/frozen——活着吗/新鲜吗/冻没冻直答 -->
      <el-table-column :label="t('liveTask.mdMode')" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.md_mode === 'hub' ? 'primary' : 'info'">{{ row.md_mode }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('liveTask.lag')" width="90" class-name="num">
        <template #default="{ row }">
          <span :style="{ color: (row.lag ?? 999) > 5 ? 'var(--warn)' : 'var(--success)' }">{{ row.lag != null ? row.lag.toFixed(1) + 's' : '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="bars" :label="t('liveTask.bars')" width="90" class-name="num" />
      <el-table-column :label="t('common.status')" width="110">
        <template #default="{ row }">
          <span style="display:inline-flex; align-items:center; gap:4px"><StatusTag :value="row.status" />{{ row.frozen ? '❄' : '' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="account_id" :label="t('common.account')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="initial_capital" :label="t('liveTask.capital')" width="120" />
            <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: var(--sp-2) 16px">
            <!-- wd-20 §1.5 方案 A：自愈时间线（task_logs 过滤渲染 + 行内事实字段） -->
            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 6px">{{ t('liveTask.selfHeal') }}:</div>
            <div style="font-size: 12px; font-family: var(--font-num)">
              {{ t('liveTask.mdMode') }}: {{ row.md_mode || '—' }} | {{ t('liveTask.lag') }}: {{ row.lag_s ?? row.lag ?? '—' }}s | {{ t('liveTask.bars') }}: {{ row.bars ?? '—' }} | {{ t('common.status') }}: {{ row.status }}
              | {{ t('liveTask.nRestarts') }}: {{ restartCount(row) }} | {{ t('liveTask.lastExit') }}: {{ lastExit(row) }}
            </div>
            <div v-if="row._timeline?.length" style="margin-top: var(--sp-2)">
              <div style="color: var(--text-secondary); font-size: 12px">{{ t('liveTask.recentLogs') }}:</div>
              <div v-for="(l, i) in row._timeline.slice(0, 8)" :key="i" style="font-size: 11px; font-family: var(--font-num); display: flex; gap: 8px">
                <span style="color: var(--text-secondary)">{{ fmtTime.s(l.ts) }}</span>
                <span :style="{ color: ['ERROR', 'error'].includes(l.level) ? 'var(--critical)' : ['WARNING', 'warning', 'WARN'].includes(l.level) ? 'var(--warn)' : 'inherit' }">{{ l.msg?.slice(0, 100) }}</span>
              </div>
            </div>
          </div>
        </template>
      </el-table-column>
<el-table-column :label="t('common.action')" width="320">
        <template #default="{ row }">
          <el-button type="primary" @click="toggleTimeline(row)">{{ t('common.detail') }}</el-button>
          <el-button type="primary" @click="gotoDetail(row.symbol)">{{ t('liveTask.symbolDetail') }}</el-button>
          <el-button v-if="row.status !== 'running'" type="success" @click="onStart(row.id)" :disabled="navReadonly">{{ t('common.start') }}</el-button>
          <el-button v-if="row.status === 'running' && row.frozen" type="warning" size="small" @click="onUnfreeze(row)" :disabled="navReadonly">{{ t('liveTask.unfreeze') }}</el-button>
          <el-button v-if="row.status === 'running'" type="danger" @click="onStop(row)" :disabled="navReadonly">{{ t('common.stop') }}</el-button>
          <el-button v-if="row.status !== 'running'" type="danger" @click="onDelete(row)" :disabled="navReadonly">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建实盘任务弹窗 -->
    <el-dialog v-model="dialogVisible" :title="t('liveTask.create')" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="120px" v-loading="saving">
        <el-form-item :label="t('liveTask.taskName')">
          <el-input v-model="form.name" :placeholder="t('liveTask.phName')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.strategy')">
          <el-select v-model="form.strategy_id" :placeholder="t('liveTask.phStrategy')" style="width: 100%" @change="onStrategyChange">
            <el-option v-for="s in strategies" :key="s.id" :label="`${s.name} (${s.id})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.symbol')">
          <!-- 链条打磨#20：标的搜索下拉（asset_static_info；此前纯手输无校验） -->
          <el-select v-model="form.symbol" filterable remote :remote-method="searchSymbols"
                     :loading="symbolSearching" :placeholder="t('liveTask.phSymbol')" style="width: 100%">
            <el-option v-for="sym in symbolOptions" :key="sym" :label="sym" :value="sym" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: var(--text-secondary); font-size: 12px; padding-left: 120px">
          {{ t('liveTask.selectStrategyFirst') }}
        </div>

        <el-divider content-position="left">{{ t('common.account') }}</el-divider>
        <el-form-item :label="t('liveTask.accountId')">
          <el-select v-model="form.account_id" :placeholder="t('liveTask.phAccountId')" style="width: 100%">
            <el-option v-for="a in accounts" :key="a.id" :label="`${a.name} (${a.id})`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="form.initial_capital" :min="10000" :step="100000" />
        </el-form-item>
      
        <!-- 批 6b：md_mode 单模式（hub），创建不再可选——direct 2026-09-01 退役 --></el-form>
      <template #footer>
        <el-button type="primary" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving" :disabled="navReadonly">{{ t('liveTask.createBtn') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { getLiveTasks, createLiveTask, startLiveTask, stopLiveTask, deleteLiveTask, getStrategies, apiErr } from '../api'
import ParameterForm from '../components/ParameterForm.vue'
import StatusTag from '../components/StatusTag.vue'
import { fmtTime } from '../utils/fmtTime'

const router = useRouter()
const route = useRoute()
const gotoDetail = symbol => router.push(`/stock/${symbol}`)
const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const tasks = ref([])
const strategies = ref([])
const accounts = ref([])
const symbolOptions = ref([])
const symbolSearching = ref(false)
const loadAccounts = async () => {
  try { accounts.value = await api.get('/account') || [] } catch { accounts.value = [] }
}
const searchSymbols = async (q) => {
  if (!q || q.length < 2) { symbolOptions.value = []; return }
  symbolSearching.value = true
  try {
    const r = await api.get('/sync/symbols/astock_daily', { params: { q, page: 1, size: 20 } })
    symbolOptions.value = (r.items || []).slice(0, 20).map(i => i.ts_code)
  } catch { symbolOptions.value = [] }
  finally { symbolSearching.value = false }
}
const dialogVisible = ref(false)
const saving = ref(false)
const parameterDefs = ref([])
const form = ref({
  name: '', strategy_id: '', symbol: '', params: {},
  account_id: '', initial_capital: 1000000,
})


const load = async () => {
  try { tasks.value = await getLiveTasks(); enrichTasks() } catch { ElMessage.error(t('common.loadFailed')) }
}   // 盲审A-P2-8：load 后 enrich（start/stop 重建 tasks 后重启数不回落）
const loadStrategies = async () => {
  try {
    // 链条打磨#20：只列 backtest_verified 策略（三级开关第三级——未验证的选了也是 403 后置暴露）
    const all = await getStrategies()
    strategies.value = (all || []).filter(s => s.backtest_verified)
  } catch { strategies.value = [] }
}

const onStrategyChange = (sid) => {
  const s = strategies.value.find(x => x.id === sid)
  if (s?.params?.parameter_defs) {
    parameterDefs.value = s.params.parameter_defs
    form.value.params = {}
  } else {
    parameterDefs.value = []
    form.value.params = {}
  }
}

const openCreate = () => {
  form.value = { name: '', strategy_id: '', symbol: '', params: {}, account_id: '', initial_capital: 1000000 }
  parameterDefs.value = []
  dialogVisible.value = true
}

const save = async () => {
  if (!form.value.name || !form.value.strategy_id || !form.value.symbol) {
    ElMessage.warning(t('liveTask.requiredHint')); return
  }
  saving.value = true
  try {
    await createLiveTask(form.value)
    ElMessage.success(t('common.createSuccess'))
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.createFailed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

// P1-5/06 B#6 冻结处置闭环:重启解冻(frozen 是 worker 态,重启清退)
const onUnfreeze = async (row) => {
  try {
    await ElMessageBox.confirm(t('liveTask.confirmUnfreeze'), t('common.confirm'), { type: 'warning' })
    await stopLiveTask(row.id); await startLiveTask(row.id)
    ElMessage.success(t('common.success')); load()
  } catch (e) { if (e?.response) ElMessage.error(t('common.failed')) }
}
const onStart = async (id) => {
  try { await startLiveTask(id); ElMessage.success(t('common.started')); load() }
  catch (e) { ElMessage.error(t('common.startFailed')) }
}
const onStop = async (row) => {
  // H5（01 P0#2/05 §5.8）：停止=影响面 confirm（确认强度对称于代价——原停止无确认、删除反有，倒挂修正）
  try {
    await ElMessageBox.confirm(t('liveTask.confirmStop'), t('common.confirm'), { type: 'warning' })
    await stopLiveTask(row.id); ElMessage.success(t('common.stopped')); load()
  } catch (e) { if (e !== 'cancel' && e?.message) ElMessage.error(t('common.stopFailed')); else if (e?.response) ElMessage.error(t('common.stopFailed')) }
}
const onDelete = async (row) => {
  // H5：删除=输入任务名确认（比停止更强——不可恢复操作）
  try {
    const { value } = await ElMessageBox.prompt(
      t('liveTask.deletePromptTip', { name: row.name }), t('liveTask.deletePromptTitle'),
      { type: 'warning', confirmButtonText: t('common.confirm') })
    if (value?.trim() !== row.name) { ElMessage.warning(t('liveTask.deleteMismatch')); return }
    await deleteLiveTask(row.id); ElMessage.success(t('common.deleteSuccess')); load()
  } catch { /* 取消 */ }
}

onMounted(async () => {
  // wd-20 §1.5：enrichTasks 移到 load 之后（原在 tasks 为空时先跑=恒空转）
  const pre = route.query.strategy   // 深链预填(回测页'创建实盘任务')
  if (pre) { dialogVisible.value = true; form.value.strategy_id = String(pre) }
  await load(); await loadStrategies(); await loadAccounts(); enrichTasks() })

// wd-20 §1.5 方案 A：自愈时间线（05 §5.8）——幽灵端点 /live-task/{id}/detail 已删
// （19 号 P0：404 恒吞）。数据源两路：①列表行已有字段（NRestarts 语义近似=心跳龄/冻结/
// md_mode 在列）②按需展开拉 task_logs?task_id= 过滤渲染
const enrichTasks = async () => {
  await Promise.all(tasks.value.map(async task => {
    try {
      const r = await api.get('/log', { params: { task_id: `live:${task.id}` } })
      task._timeline = (r?.logs || []).slice(0, 100)   // 盲审A-P2-8：满窗计数（渲染侧 slice(0,8)）
    } catch { task._timeline = [] }
  }))
}
// wd-20 §1.5 裁定②：重启/退出码由 task_logs 时间线派生（启动条目数-1=重启数）
const restartCount = row => Math.max((row._timeline || []).filter(l => (l.msg || '').includes('任务启动')).length - 1, 0)
const lastExit = row => {
  const e = (row._timeline || []).find(l => (l.msg || '').includes('退出'))
  return e ? (e.msg.includes('退出码 0') ? '0' : e.msg.slice(0, 24)) : '—'
}
const toggleTimeline = async (row) => {
  row._open = !row._open
  if (row._open && row._timeline == null) {
    try {
      const r = await api.get('/log', { params: { task_id: `live:${row.id}` } })
      row._timeline = (r?.logs || []).slice(0, 8)
    } catch { row._timeline = [] }
  }
}

// P1-5(05 §5.8):自愈时间线数据(NRestarts)+快照查看

</script>
