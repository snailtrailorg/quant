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

    <!-- P1-2（web-design 05 §5.4）：差异处置台——结构化差异单+处置状态持久化+行展开证据链 -->
    <el-table :data="diffRows" stripe size="small" row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: 4px 12px; color: var(--text-secondary); font-size: var(--fs-foot)">
            <div>{{ t('reconcile.firstSeen') }}: {{ row.first_seen || '—' }}</div>
            <div>{{ t('reconcile.evidence') }}: {{ row.detail || '—' }}</div>
            <div v-if="row.note">{{ t('reconcile.note') }}: {{ row.note }}</div>
            <div v-if="row.exempt_qty != null">{{ t('reconcile.exemptInfo', { q: row.exempt_qty, d: row.exempt_until || '—' }) }}</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="symbol" label="Symbol" width="110" />
      <el-table-column :label="t('reconcile.issueType')" width="130">
        <template #default="{ row }">{{ issueTypeLabel(row.issue_type) }}</template>
      </el-table-column>
      <el-table-column prop="broker_qty" :label="t('reconcile.brokerQty')" width="110" class-name="num" />
      <el-table-column prop="derived_qty" :label="t('reconcile.derivedQty')" width="110" class-name="num" />
      <el-table-column :label="t('reconcile.diff')" width="100" class-name="num">
        <template #default="{ row }">
          <span v-if="row.broker_qty != null" :class="(row.broker_qty - row.derived_qty) >= 0 ? 'up' : 'down'">
            {{ (row.broker_qty - row.derived_qty) >= 0 ? '▲' : '▼' }}{{ Math.abs(row.broker_qty - row.derived_qty) }}
          </span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column prop="first_seen" :label="t('reconcile.firstSeen')" width="160" />
      <el-table-column :label="t('common.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="{ open: 'danger', verified: 'success', ignored: 'info', exempt: 'warning' }[row.status] || 'info'" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="240" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'open' && canHandle">
            <el-button size="small" @click="act(row, 'verify')">{{ t('reconcile.verify') }}</el-button>
            <el-button size="small" @click="act(row, 'exempt')">{{ t('reconcile.exempt') }}</el-button>
            <el-button size="small" type="info" plain @click="act(row, 'ignore')">{{ t('reconcile.ignore') }}</el-button>
          </template>
          <span v-else style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ row.handled_by || '—' }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="!diffRows.length" style="color: var(--text-secondary); padding: 16px 0">{{ t('reconcile.noIssue') }}</div>

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
const hasIssues = computed(() => diffRows.value.some(r => r.status === 'open'))
const summary = computed(() => hasIssues.value
  ? t('reconcile.openCount', { n: diffRows.value.filter(r => r.status === 'open').length })
  : t('reconcile.allConsistent'))

const issueTypeLabel = ty => ({ position_diff: t('reconcile.tPositionDiff'), manual_order: t('reconcile.tManual'),
  signal_no_order: t('reconcile.tSignalNoOrder'), order_no_trade: t('reconcile.tOrderNoTrade') }[ty] || ty)
const statusLabel = st => ({ open: t('reconcile.stOpen'), verified: t('reconcile.stVerified'),
  ignored: t('reconcile.stIgnored'), exempt: t('reconcile.stExempt') }[st] || st)

const loadDiff = async () => {
  try { diffRows.value = (await api.get('/reconcile/issues')).items || [] }
  catch { diffRows.value = [] }
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
