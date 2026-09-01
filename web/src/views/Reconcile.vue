<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('reconcile.title') }}</span>
        <div>
          <el-button size="small" @click="openManual = true">{{ t('reconcile.manualOrder') }}</el-button>
          <el-button size="small" type="warning" @click="onReset">{{ t('reconcile.resetBtn') }}</el-button>
          <el-button type="primary" @click="rerun">{{ t('reconcile.rerun') }}</el-button>
        </div>
      </div>
    </template>
    <el-alert :title="summary" :type="hasIssues ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />

    <!-- W6：el-table-v2 虚拟滚动（≤500 行）+行点击详情抽屉（原 expand 列 v2 不支持——盲审重构）；
         脱敏分支（count/aggregated 摘要，同 Risk 页范式） -->
    <el-alert v-if="issuesSens && issuesSens !== 'detail'" type="info" :closable="false" style="margin: 8px 0">
      {{ t('perm.sensLimited') }}: {{ issuesSens }} — {{ issuesSensSummary }}
    </el-alert>
    <el-auto-resizer v-else>
      <template #default="{ width }">
        <el-table-v2 :columns="issueCols" :data="diffRows" :width="width" :height="480"
                     :row-height="48" fixed :row-event-handlers="{ onClick: ({ rowData }) => openDetail(rowData) }"
                     :row-class="({ rowIndex }) => rowIndex % 2 ? 'v2-zebra' : ''" />
      </template>
    </el-auto-resizer>
    <div v-if="!diffRows.length" style="color: var(--text-secondary); padding: 16px 0">{{ t('reconcile.noIssue') }}</div>
    <!-- 行详情抽屉（原 expand 面板 6 行证据链） -->
    <el-drawer v-model="detailVisible" :title="t('reconcile.evidence')" size="360px">
      <template v-if="detailRow">
        <div style="color: var(--text-secondary); font-size: var(--fs-foot); line-height: 2">
          <div>{{ t('reconcile.firstSeen') }}: {{ detailRow.first_seen || '—' }}</div>
          <div>{{ t('reconcile.evidence') }}: {{ detailRow.detail || '—' }}</div>
          <div>{{ t('reconcile.ordersFlow') }}: <el-link type="primary" @click="$router.push(`/trading`)">{{ t('reconcile.viewOrders') }}</el-link></div>
          <div>{{ t('reconcile.posSnapshot') }}: <el-link type="primary" @click="$router.push(`/trading`)">{{ t('reconcile.viewPositions') }}</el-link></div>
          <div v-if="detailRow.note">{{ t('reconcile.note') }}: {{ detailRow.note }}</div>
          <div v-if="detailRow.exempt_qty != null">{{ t('reconcile.exemptInfo', { q: detailRow.exempt_qty, d: detailRow.exempt_until || '—' }) }}</div>
        </div>
      </template>
    </el-drawer>

    <!-- 原始 issues 摘要（兼容期保留——旧字符串通道，勿误修#11） -->
    <el-collapse v-if="rawIssues.length" style="margin-top: 12px">
      <el-collapse-item :title="t('reconcile.rawIssues', { n: rawIssues.length })">
        <div v-for="(it, i) in rawIssues" :key="i" style="font-size: var(--fs-foot); color: var(--text-secondary)">{{ it }}</div>
      </el-collapse-item>
    </el-collapse>

    <!-- 登记豁免（标的级）：数量+生效期+原因 -->
    <el-dialog v-model="exemptDlg" :title="t('reconcile.exemptTitle')" width="420px">
      <el-form label-width="110px">
        <el-form-item :label="t('reconcile.exemptQty')"><el-input-number v-model="exemptForm.exempt_qty" :step="100" /></el-form-item>
        <el-form-item :label="t('reconcile.exemptUntil')"><el-date-picker v-model="exemptForm.exempt_until" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item :label="t('reconcile.note')"><el-input v-model="exemptForm.reason" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <div style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('reconcile.exemptHint') }}</div>
      <template #footer>
        <el-button @click="exemptDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="submitExempt">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>

    <!-- 场外单登记 -->
    <el-dialog v-model="openManual" :title="t('reconcile.manualOrder')" width="420px">
      <el-form label-width="90px">
        <el-form-item label="Symbol"><el-input v-model="manualForm.symbol" placeholder="600000" /></el-form-item>
        <el-form-item :label="t('reconcile.volume')"><el-input-number v-model="manualForm.volume" :step="100" /></el-form-item>
        <el-form-item :label="t('reconcile.note')"><el-input v-model="manualForm.note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="openManual = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="submitManual">{{ t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getReconcile } from '../api'
import api from '../api'

const { t } = useI18n()
const canHandle = ['trader', 'admin'].includes(localStorage.getItem('role') || 'viewer')
const rawIssues = ref([])
const diffRows = ref([])
const detailVisible = ref(false)
const detailRow = ref(null)
const openDetail = row => { detailRow.value = row; detailVisible.value = true }
const issuesSens = ref('detail')
const issuesSensSummary = ref('')
// W6 v2 列：i18n 与组件渲染入 JS(cellRenderer)——操作列 h(ElButton) 保组件形态
import { h } from 'vue'
import { ElButton, ElTag } from 'element-plus'
const issueCols = computed(() => [
  { key: 'symbol', dataKey: 'symbol', title: 'Symbol', width: 110 },
  { key: 'issue_type', dataKey: 'issue_type', title: t('reconcile.issueType'), width: 130,
    cellRenderer: ({ cellData }) => issueTypeLabel(cellData) },
  { key: 'broker_qty', dataKey: 'broker_qty', title: t('reconcile.brokerQty'), width: 110, align: 'right' },
  { key: 'derived_qty', dataKey: 'derived_qty', title: t('reconcile.derivedQty'), width: 110, align: 'right' },
  { key: 'diff', dataKey: 'broker_qty', title: t('reconcile.diff'), width: 100, align: 'right',
    cellRenderer: ({ rowData }) => rowData.broker_qty == null ? '—'
      : `${(rowData.broker_qty - rowData.derived_qty) >= 0 ? '▲' : '▼'}${Math.abs(rowData.broker_qty - rowData.derived_qty)}` },
  { key: 'first_seen', dataKey: 'first_seen', title: t('reconcile.firstSeen'), width: 160 },
  { key: 'status', dataKey: 'status', title: t('common.status'), width: 100,
    cellRenderer: ({ cellData }) => h(ElTag, { size: 'small',
      type: ({ open: 'danger', verified: 'success', ignored: 'info', exempt: 'warning' })[cellData] || 'info' },
      () => statusLabel(cellData)) },
  { key: 'action', dataKey: 'status', title: t('common.action'), width: 250, fixed: 'right',
    cellRenderer: ({ rowData }) => {
      if (rowData.status === 'open' && canHandle) {
        return h('span', { style: 'display:flex;gap:6px' }, [
          h(ElButton, { size: 'small', onClick: e => { e.stopPropagation(); act(rowData, 'verify') } }, () => t('reconcile.verify')),
          h(ElButton, { size: 'small', onClick: e => { e.stopPropagation(); act(rowData, 'exempt') } }, () => t('reconcile.exempt')),
          h(ElButton, { size: 'small', type: 'info', plain: true, onClick: e => { e.stopPropagation(); act(rowData, 'ignore') } }, () => t('reconcile.ignore')),
        ])
      }
      return h('span', { style: 'color:var(--text-secondary);font-size:var(--fs-foot)' }, rowData.handled_by || '—')
    } },
])
const hasIssues = computed(() => diffRows.value.some(r => r.status === 'open'))
const summary = computed(() => hasIssues.value
  ? t('reconcile.openCount', { n: diffRows.value.filter(r => r.status === 'open').length })
  : t('reconcile.allConsistent'))

const issueTypeLabel = ty => ({ position_diff: t('reconcile.tPositionDiff'), manual_order: t('reconcile.tManual'),
  signal_no_order: t('reconcile.tSignalNoOrder'), order_no_trade: t('reconcile.tOrderNoTrade') }[ty] || ty)
const statusLabel = st => ({ open: t('reconcile.stOpen'), verified: t('reconcile.stVerified'),
  ignored: t('reconcile.stIgnored'), exempt: t('reconcile.stExempt') }[st] || st)

const loadDiff = async () => {
  try {
    const r = await api.get('/reconcile/issues')
    issuesSens.value = r.sensitivity || 'detail'
    diffRows.value = r.items || []
    if (r.sensitivity === 'count')
      issuesSensSummary.value = `${r.count} ${t('perm.sensCountUnit')}`
    else if (r.sensitivity === 'aggregated')
      issuesSensSummary.value = Object.entries(r.by_status || {}).map(([k, v]) => `${statusLabel(k) || k}: ${v}`).join(' · ')
  } catch { diffRows.value = []; issuesSens.value = 'detail' }
}
const load = async () => {
  try { const r = await getReconcile(); rawIssues.value = r.issues || [] } catch { rawIssues.value = [] }
  await loadDiff()
}
const rerun = async () => {
  try { await getReconcile(); await loadDiff(); ElMessage.success(t('reconcile.rerunDone')) }
  catch { ElMessage.error(t('reconcile.queryFailed')) }
}

const act = async (row, kind) => {
  if (kind === 'exempt') {
    exemptForm.value = { id: row.id, exempt_qty: Math.abs((row.broker_qty || 0) - (row.derived_qty || 0)), exempt_until: '', reason: '' }
    exemptDlg.value = true; return
  }
  try {
    await api.post(`/reconcile/issues/${row.id}/${kind}`)
    ElMessage.success(t('common.success')); await loadDiff()
  } catch { ElMessage.error(t('common.failed')) }
}

const exemptDlg = ref(false)
const exemptForm = ref({ id: 0, exempt_qty: 0, exempt_until: '', reason: '' })
const submitExempt = async () => {
  try {
    await api.post(`/reconcile/issues/${exemptForm.value.id}/exempt`, exemptForm.value)
    exemptDlg.value = false; ElMessage.success(t('common.success')); await loadDiff()
  } catch { ElMessage.error(t('common.failed')) }
}

const openManual = ref(false)
const manualForm = ref({ symbol: '', volume: 0, note: '' })
const submitManual = async () => {
  try {
    await api.post('/reconcile/manual-order', manualForm.value)
    openManual.value = false; ElMessage.success(t('common.success')); await loadDiff()
  } catch { ElMessage.error(t('common.failed')) }
}

const onReset = async () => {
  try {
    const { value } = await ElMessageBox.prompt(t('reconcile.resetPromptTip'), t('reconcile.resetPromptTitle'), { type: 'warning' })
    if (value?.trim() !== 'RESET') { ElMessage.warning(t('risk.resumeMismatch')); return }
  } catch { return }
  try { await api.post('/reconcile/reset'); ElMessage.success(t('common.success')); await loadDiff() }
  catch { ElMessage.error(t('common.failed')) }
}

onMounted(load)
</script>
