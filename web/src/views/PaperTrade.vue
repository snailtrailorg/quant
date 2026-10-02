<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('paperTrade.title') }}</span>
        <div style="display: flex; gap: 8px; align-items: center">
          <ColumnSettings storage-key="cols.paper-tasks" :columns="taskColDefs" v-model:visible="taskVisible" />
          <IconBtn v-if="canPaper" :icon="Plus" :title="t('paperTrade.create')" @click="openCreate" />
        </div>
      </div>
    </template>

    <!-- 批 86-B：边界声明（设计裁定=页面级明示，不得折叠）。依据：行业文献一致表明
         paper 表现系统性高估实盘盈利（20–40%）；页面若不写明，纸上曲线会被读成「实盘预演」。
         此页能力边界三句：实时行情/完整风控链/订单不进市场 + 结论用途限定。 -->
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: var(--sp-3)">
      <template #title>{{ t('paperTrade.boundary') }}</template>
    </el-alert>

    <TableShell :data="tasks" storage-key="paper-tasks" @expand-change="onExpand">
      <el-table-column v-if="colOn('id')" prop="id" label="ID" width="80" />
      <el-table-column :label="t('common.name')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <router-link v-if="row.symbol" :to="`/stock/${row.symbol}`">{{ row.name }}</router-link>
          <span v-else>{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('strategy_id')" prop="strategy_id" :label="t('liveTask.strategy')" min-width="120" show-overflow-tooltip />
      <el-table-column v-if="colOn('symbol')" prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip />
      <el-table-column v-if="colOn('bars')" prop="bars" :label="t('liveTask.bars')" width="90" class-name="num" />
      <el-table-column v-if="colOn('status')" prop="status" :label="t('common.status')" width="110">
        <template #default="{ row }">
          <span style="display:inline-flex; align-items:center; gap:4px"><StatusTag :value="row.status" />{{ row.frozen ? '❄' : '' }}</span>
        </template>
      </el-table-column>
      <!-- 账户列恒显示且不可关：纸上任务固定绑定系统虚拟账户（0127），列出来就是
           「这页的数据不与实盘共账」的常驻证据；隐藏它反而让人以为可选账户。 -->
      <el-table-column prop="account_name" :label="t('liveTask.account')" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">
          <span>{{ row.account_name }} <el-tag size="small" type="info">{{ t('paperTrade.virtualTag') }}</el-tag></span>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('initial_capital')" prop="initial_capital" :label="t('liveTask.capital')" min-width="120" />
      <el-table-column v-if="colOn('created_at')" prop="created_at" :label="t('common.createdAt')" min-width="220">
        <template #default="{ row }">{{ fmtTime.full(row.created_at) }}</template>
      </el-table-column>

      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: var(--sp-2) 16px">
            <!-- 与实盘任务页同构：自愈时间线（task_logs 过滤渲染）——paper 走同一条 runner
                 运行时路径，日志同落 task_logs（task_id 前缀 live:），复用同一读法。 -->
            <div style="font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: 6px">{{ t('liveTask.selfHeal') }}:</div>
            <div style="font-size: var(--fs-foot); font-family: var(--font-num)">
              {{ t('liveTask.bars') }}: {{ row.bars ?? '—' }} | {{ t('common.status') }}: {{ row.status }}
            </div>
            <div v-if="row._timeline?.length" style="margin-top: var(--sp-2)">
              <div style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('liveTask.recentLogs') }}:</div>
              <div v-for="(l, i) in row._timeline.slice(0, 8)" :key="i" style="font-size: var(--fs-foot); font-family: var(--font-num); display: flex; gap: 8px">
                <span style="color: var(--text-secondary)">{{ fmtTime.s(l.ts) }}</span>
                <span :style="{ color: ['ERROR', 'error'].includes(l.level) ? 'var(--critical)' : ['WARNING', 'warning', 'WARN'].includes(l.level) ? 'var(--warn)' : 'inherit' }">{{ l.msg?.slice(0, 100) }}</span>
              </div>
            </div>
            <!-- paper 任务同样走 hub 行情链 ⇒ 同样会被数据质量冻结（ts_gap/seq_gap/untrusted）。
                 解冻语义与实盘完全一致（接受当前数据状态，不重启），判权走 assert_task_perm(mode=paper)
                 ⇒ paper_trade——本页用户天然持有。 -->
            <div style="margin-top: var(--sp-2); border-top: 1px solid var(--border-light); padding-top: var(--sp-2)">
              <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-bottom: 6px">{{ t('liveTask.freezeHistory') }}</div>
              <div v-if="row._freeze === null" style="font-size: var(--fs-foot); color: var(--text-secondary)">{{ t('liveTask.freezeNoPerm') }}</div>
              <div v-else-if="!row._freeze?.length" style="font-size: var(--fs-foot); color: var(--text-secondary)">{{ t('liveTask.freezeNone') }}</div>
              <div v-else style="font-size: var(--fs-foot); font-family: var(--font-num)">
                <div v-for="e in row._freeze" :key="e.id" :class="e.unfrozen_at ? 'fz-closed' : 'fz-open'" style="display: flex; gap: 8px; padding: var(--sp-1) 0">
                  <span style="min-width: 150px">{{ fmtTime.s(e.frozen_at) }}</span>
                  <span style="min-width: 68px">{{ t(FT[e.freeze_type] || 'liveTask.ftUnknown') }}</span>
                  <span style="min-width: 150px">{{ e.unfrozen_at ? fmtTime.s(e.unfrozen_at) : t('liveTask.inProgress') }}</span>
                  <IconBtn v-if="!e.unfrozen_at && canPaper" size="small" type="warning" :icon="Unlock"
                           :title="t('liveTask.unfreeze')" @click="onUnfreeze(row)" :disabled="navReadonly" />
                </div>
              </div>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column prop="actions" :label="t('common.action')" width="200" fixed="right">
        <template #default="{ row }">
          <!-- 颜色语义与 LiveTask 同约：success=启 / danger=停·删 / warning=解冻 / ''=看·改 -->
          <IconBtn size="small" type="success" :icon="VideoPlay" :title="t('common.start')"
                   v-if="row.status !== 'running' && canPaper" @click="onStart(row.id)" :disabled="navReadonly" />
          <IconBtn size="small" type="danger" :icon="VideoPause" :title="t('common.stop')"
                   v-if="row.status === 'running' && canPaper" @click="onStop(row)" :disabled="navReadonly" />
          <!-- 查看结果：本页真实能力（paper_trade_log 已实现权益曲线）——与 LiveTask 页的
               disabled 占位不同，这里点开即有数据（无成交时空态兜底）。 -->
          <IconBtn size="small" :icon="TrendCharts" :title="t('paperTrade.viewResult')" @click="openEquity(row)" />
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')"
                   v-if="row.status !== 'running' && canPaper" @click="openEdit(row)" :disabled="navReadonly" />
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')"
                   v-if="row.status !== 'running' && canPaper" @click="openDelete(row)" :disabled="navReadonly" />
        </template>
      </el-table-column>
    </TableShell>

    <!-- 删除强确认（输入任务名，站内最强确认——与实盘任务页同一约定） -->
    <el-dialog v-model="delVisible" :title="t('liveTask.deleteTitle')" width="420px" :close-on-click-modal="false">
      <el-form @submit.prevent>
        <el-form-item :label="t('liveTask.deletePromptTip', { name: delRow?.name })">
          <el-input v-model="deleteConfirmName" :placeholder="delRow?.name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="delVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :disabled="!delRow || deleteConfirmName.trim() !== delRow.name" @click="onDelete(delRow)">{{ t('common.delete') }}</el-button>
      </template>
    </el-dialog>

    <!-- 创建纸上任务弹窗：与实盘弹窗的差异只有一处——**无账户选择**。
         账户由后端强制绑定系统虚拟账户（0127，is_virtual=true；前端传 account_id 会被忽略），
         这里不放假下拉（假控件=可点不可用的死壳）。 -->
    <el-dialog v-model="dialogVisible" :title="t('paperTrade.create')" width="720px" :close-on-click-modal="false">
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
          <el-select v-model="form.symbol" filterable remote :remote-method="searchSymbols"
                     :loading="symbolSearching" :placeholder="t('liveTask.phSymbol')" style="width: 100%">
            <el-option v-for="sym in symbolOptions" :key="sym" :label="sym" :value="sym" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: var(--text-secondary); font-size: var(--fs-foot); padding-left: 120px">
          {{ t('liveTask.selectStrategyFirst') }}
        </div>

        <el-divider content-position="left">{{ t('liveTask.account') }}</el-divider>
        <el-form-item :label="t('liveTask.accountId')">
          <el-input :model-value="t('paperTrade.virtualAccount')" disabled />
          <div style="font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5">
            {{ t('paperTrade.virtualAccountNote') }}
          </div>
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="form.initial_capital" v-bind="CAPITAL_INPUT" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving" :disabled="navReadonly">{{ t('liveTask.createBtn') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑弹窗：仅 name/initial_capital/params——strategy_id/symbol/account_id/mode 不可改
         （决定任务身份：systemd 单元名绑 tid、策略快照建任务时固化、账户决定风控预算归属）。
         后端对不可改字段显式 400 FIELD_IMMUTABLE。参数定义取自策略当前定义；若策略在任务
         创建后被改过，服务端按**策略快照**校验（真源），以 400 报错为准。 -->
    <el-dialog v-model="editVisible" :title="t('paperTrade.editTitle')" width="720px" :close-on-click-modal="false">
      <el-form :model="editForm" label-width="120px" v-loading="saving">
        <el-form-item :label="t('liveTask.taskName')">
          <el-input v-model="editForm.name" :placeholder="t('liveTask.phName')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="editForm.initial_capital" v-bind="CAPITAL_INPUT" />
        </el-form-item>
        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="editDefs.length" :defs="editDefs" v-model="editForm.params" />
        <div v-else style="color: var(--text-secondary); font-size: var(--fs-foot)">
          {{ t('paperTrade.noParamDefs') }}
        </div>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="editVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving" :disabled="navReadonly">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 权益曲线弹窗：**已实现**口径（paper_trade_log 现金流回算），浮动盈亏不并入——
         后端端点注释明文要求前端分开标注，合成一条「总权益」=把估算冒充精确。 -->
    <el-dialog v-model="eqVisible" :title="t('paperTrade.equityTitle', { name: eqRow?.name })" width="760px" :close-on-click-modal="false">
      <div style="margin-bottom: var(--sp-2); color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('paperTrade.equityNote') }}</div>
      <div style="display: flex; gap: 24px; margin-bottom: var(--sp-2); font-size: var(--fs-foot)">
        <span>{{ t('paperTrade.initCapital') }}: <b class="num">¥{{ fmtNum(eqData?.initial_capital) }}</b></span>
        <span>{{ t('paperTrade.realizedPnl') }}:
          <b class="num" :class="pnlClass(eqData?.realized_pnl)">{{ pnlArrow(eqData?.realized_pnl) }} ¥{{ fmtNum(eqData?.realized_pnl) }}</b></span>
        <span>{{ t('paperTrade.tradeCount') }}: <b class="num">{{ eqData?.trade_count ?? 0 }}</b></span>
      </div>
      <v-chart v-if="eqPoints.length" :option="eqOption" autoresize style="height: 320px" />
      <EmptyState v-else size="small" :description="t('paperTrade.noTrades')" />
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { getPaperTasks, getPaperEquity, createLiveTask, updateLiveTask, startLiveTask, stopLiveTask, deleteLiveTask, getFreezeEvents, unfreezeLiveTask, getStrategies, apiErr } from '../api'
import { CAPITAL_INPUT } from '../utils/inputRanges'
import ParameterForm from '../components/ParameterForm.vue'
import StatusTag from '../components/StatusTag.vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import EmptyState from '../components/EmptyState.vue'
import { Plus, Unlock, VideoPlay, VideoPause, TrendCharts, Edit, Delete } from '@element-plus/icons-vue'
import { fmtTime } from '../utils/fmtTime'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
// 批 86-B：本页动作门=paper_trade（与后端 paper 端点同键）。页面级路由门已挡无权者，
// canPerm 只做按钮显隐（UI 提示层，不放宽任何东西）。
const canPerm = inject('canPerm', () => true)
const canPaper = computed(() => canPerm('paper_trade'))

const tasks = ref([])
const strategies = ref([])
const symbolOptions = ref([])
const symbolSearching = ref(false)

const searchSymbols = async (q) => {
  if (!q || q.length < 2) { symbolOptions.value = []; return }
  symbolSearching.value = true
  try {
    const r = await api.get('/sync/symbols/astock_daily', { params: { q, page: 1, size: 20 } })
    symbolOptions.value = (r.items || []).slice(0, 20).map(i => i.ts_code)
  } catch { symbolOptions.value = [] }
  finally { symbolSearching.value = false }
}

// 列显隐（与 LiveTask 同机制，键独立——两页关注列不同：paper 不显示 lag/md_mode）
const taskColDefs = computed(() => [
  { key: 'id', label: 'ID' },
  { key: 'strategy_id', label: t('liveTask.strategy') },
  { key: 'symbol', label: t('common.symbol') },
  { key: 'bars', label: t('liveTask.bars'), hidden: true },
  { key: 'status', label: t('common.status') },
  { key: 'initial_capital', label: t('liveTask.capital'), hidden: true },
  { key: 'created_at', label: t('common.createdAt') },
])
const taskVisible = ref([])
const colOn = k => taskVisible.value.includes(k)

const load = async () => {
  try { tasks.value = await getPaperTasks(); enrichTasks(); preloadFreeze() } catch { ElMessage.error(t('common.loadFailed')) }
}
const loadStrategies = async () => {
  try {
    // 与实盘创建同源：只列 backtest_verified 策略（三级开关第三级）
    const all = await getStrategies()
    strategies.value = (all || []).filter(s => s.backtest_verified)
  } catch { strategies.value = [] }
}

const onStrategyChange = (sid) => {
  const s = strategies.value.find(x => x.id === sid)
  parameterDefs.value = s?.params?.parameter_defs || []
  form.value.params = {}
}

// ---- 创建（paper）----
const dialogVisible = ref(false)
const saving = ref(false)
const parameterDefs = ref([])
const form = ref({ name: '', strategy_id: '', symbol: '', params: {}, initial_capital: 1000000 })
const openCreate = () => {
  form.value = { name: '', strategy_id: '', symbol: '', params: {}, initial_capital: 1000000 }
  parameterDefs.value = []
  dialogVisible.value = true
}
const save = async () => {
  if (!form.value.name || !form.value.strategy_id || !form.value.symbol) {
    ElMessage.warning(t('paperTrade.requiredHint')); return
  }
  saving.value = true
  try {
    // mode='paper' 随体提交；后端按 mode 分档判权并强制绑定虚拟账户
    await createLiveTask({ ...form.value, mode: 'paper' })
    ElMessage.success(t('common.createSuccess'))
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.createFailed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

// ---- 编辑（paper）----
const editVisible = ref(false)
const editRow = ref(null)
const editDefs = ref([])
const editForm = ref({ name: '', initial_capital: 0, params: {} })
const openEdit = (row) => {
  editRow.value = row
  editForm.value = {
    name: row.name,
    initial_capital: row.initial_capital ?? 0,
    params: { ...(row.params || {}) },   // 现值回填（深拷贝防表格行被就地改）
  }
  const s = strategies.value.find(x => x.id === row.strategy_id)
  editDefs.value = s?.params?.parameter_defs || []
  editVisible.value = true
}
const saveEdit = async () => {
  if (!editForm.value.name?.trim()) { ElMessage.warning(t('paperTrade.nameRequired')); return }
  saving.value = true
  try {
    await updateLiveTask(editRow.value.id, {
      name: editForm.value.name,
      initial_capital: editForm.value.initial_capital,
      params: editForm.value.params,
    })
    ElMessage.success(t('common.save') + ' ✓')
    editVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.failed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

// ---- 删除（强确认）----
const delRow = ref(null)
const delVisible = computed({
  get: () => !!delRow.value,
  set: v => { if (!v) delRow.value = null },
})
const deleteConfirmName = ref('')
const openDelete = (row) => { delRow.value = row; deleteConfirmName.value = ''; delVisible.value = true }
const onDelete = async (row) => {
  if (!row) return
  try {
    await deleteLiveTask(row.id)
    ElMessage.success(t('common.deleteSuccess'))
    delRow.value = null
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

// ---- 启停/解冻（语义与实盘页同源，判权按 mode=paper → paper_trade）----
const onStart = async (id) => {
  try { await startLiveTask(id); ElMessage.success(t('common.started')); load() }
  catch (e) { ElMessage.error(t('common.startFailed')) }
}
const onStop = async (row) => {
  try {
    await ElMessageBox.confirm(t('liveTask.confirmStop'), t('common.confirm'), { type: 'warning' })
    await stopLiveTask(row.id); ElMessage.success(t('common.stopped')); load()
  } catch (e) { if (e !== 'cancel' && e?.message) ElMessage.error(t('common.stopFailed')); else if (e?.response) ElMessage.error(t('common.stopFailed')) }
}
// 解冻=人工接受当前数据状态（不重启任务）；下发后不立即重拉（闭环 5s 延迟，拉了仍是「进行中」=误导）
const onUnfreeze = async (row) => {
  try {
    await ElMessageBox.confirm(t('liveTask.confirmUnfreeze'), t('common.confirm'), { type: 'warning' })
    await unfreezeLiveTask(row.id)
    ElMessage.success(t('liveTask.unfreezeSent'))
  } catch (e) { if (e?.response) ElMessage.error(t('common.failed')) }
}
const loadFreeze = async (tid) => {
  const row = tasks.value.find(x => x.id === tid)
  if (!row) return
  try {
    const r = await getFreezeEvents(tid, 20)
    row._freeze = r?.events || []
  } catch (e) { row._freeze = e?.response?.status === 403 ? null : [] }
}
const freezing = row => (row._freeze || []).filter(e => !e.unfrozen_at)
const onExpand = (row, expandedRows) => {
  let open
  if (Array.isArray(expandedRows)) open = expandedRows.some(r => r?.id === row?.id)
  else open = expandedRows === true
  if (open && row._freeze === undefined) loadFreeze(row.id)
}
const FT = { ts_gap: 'liveTask.ftTsGap', seq_gap: 'liveTask.ftSeqGap', untrusted: 'liveTask.ftUntrusted' }
const preloadFreeze = () => {
  tasks.value.filter(x => x.status === 'running' && (x._freeze === undefined || freezing(x).length))
    .forEach(x => loadFreeze(x.id))
}

// ---- 权益曲线（已实现口径）----
const eqVisible = ref(false)
const eqRow = ref(null)
const eqData = ref(null)
const eqPoints = computed(() => eqData.value?.points || [])
const openEquity = async (row) => {
  eqRow.value = row
  eqData.value = null
  eqVisible.value = true
  try { eqData.value = await getPaperEquity(row.id) }
  catch (e) { ElMessage.error(t('common.loadFailed') + ': ' + apiErr(e)) }
}
const pnlClass = v => (v || 0) >= 0 ? 'up' : 'down'
const pnlArrow = v => (v || 0) >= 0 ? '▲' : '▼'
const fmtNum = v => (Number(v) || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
const eqOption = computed(() => ({
  grid: { left: 70, right: 16, top: 16, bottom: 28 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: eqPoints.value.map(p => (p.ts || '').replace('T', ' ').slice(5, 16)) },
  yAxis: { type: 'value', scale: true, axisLabel: { formatter: v => (v / 1e4).toFixed(0) + t('trading.wanUnit') } },
  series: [{
    name: t('paperTrade.equitySeries'),
    type: 'line', data: eqPoints.value.map(p => p.equity), smooth: true, showSymbol: false,
    lineStyle: { width: 2 }, areaStyle: { opacity: 0.08 },
  }],
}))

// 自愈时间线（与实盘页同读法：task_logs?task_id=live:{tid}——runner 对 paper 任务同前缀落账）
const enrichTasks = async () => {
  await Promise.all(tasks.value.map(async task => {
    try {
      const r = await api.get('/log', { params: { task_id: `live:${task.id}` } })
      task._timeline = (r?.logs || []).slice(0, 100)
    } catch (e) { task._timeline = e?.response?.status === 403 ? null : [] }
  }))
}

onMounted(async () => {
  await Promise.all([load(), loadStrategies()])
})
</script>

<style scoped>
/* 与 LiveTask 同款冻结行高亮——color-mix 派生透明底（不引新 hex，令牌门只许减不许增） */
.fz-open { background: color-mix(in srgb, var(--warn-fill) 14%, transparent); color: var(--warn); }
.fz-closed { color: inherit; }
</style>
