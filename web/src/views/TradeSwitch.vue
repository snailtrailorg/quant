<template>
  <!-- 批 61 M6：账号切换会话页（半自动立法——提名信息包人审+检查单复评+原子切换） -->
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <template #header><span style="font-weight: 600">{{ t('tradeSwitch.title') }}</span></template>
      <el-alert type="info" :title="t('tradeSwitch.tip')" :closable="false" style="margin-bottom: 12px" />
      <div style="display: flex; gap: 8px; margin-bottom: 12px">
        <el-button type="primary" size="small" @click="openNominate">{{ t('tradeSwitch.nominate') }}</el-button>
        <el-checkbox v-model="activeOnly" size="small" style="margin-left: 8px"
                     @change="load">{{ t('tradeSwitch.activeOnly') }}</el-checkbox>
      </div>
      <TableShell :data="sessions" storage-key="trade-switch-sessions" size="small">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column :label="t('tradeSwitch.colState')" width="110">
          <template #default="{ row }">
            <el-tag :type="stateTag(row.state)" size="small">{{ stateText(row.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('tradeSwitch.colFrom')" min-width="150">
          <template #default="{ row }">{{ accountLabel(row.from_account) }}</template>
        </el-table-column>
        <el-table-column :label="t('tradeSwitch.colTo')" min-width="150">
          <template #default="{ row }">{{ accountLabel(row.to_account) }}</template>
        </el-table-column>
        <el-table-column :label="t('tradeSwitch.colDeadline')" width="150">
          <template #default="{ row }">
            <span v-if="row.state === 'nominated'">{{ countdown(row) }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="nominated_at" :label="t('tradeSwitch.colNominatedAt')" min-width="160"
                         :formatter="r => fmtTime(r.nominated_at)" />
        <el-table-column width="240" align="right">
          <template #default="{ row }">
            <el-button size="small" @click="openDetail(row)">{{ t('tradeSwitch.detail') }}</el-button>
            <el-button v-if="row.state === 'nominated'" size="small" type="primary"
                       @click="doConfirm(row)">{{ t('tradeSwitch.confirm') }}</el-button>
            <el-button v-if="row.state === 'nominated'" size="small"
                       @click="doExtend(row)">{{ t('tradeSwitch.extend') }}</el-button>
            <el-button v-if="row.state === 'confirmed'" size="small" type="warning"
                       @click="openExecute(row)">{{ t('tradeSwitch.execute') }}</el-button>
            <el-button v-if="row.state === 'nominated' || row.state === 'confirmed'" size="small" type="danger"
                       @click="doAbort(row)">{{ t('tradeSwitch.abort') }}</el-button>
          </template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- 提名对话框 -->
    <el-dialog v-model="nom.visible" :title="t('tradeSwitch.nomTitle')" width="480px">
      <el-form label-width="90px">
        <el-form-item :label="t('tradeSwitch.fFrom')">
          <el-select v-model="nom.from" style="width: 100%">
            <el-option v-for="a in tradingAccounts" :key="a.id" :value="a.id" :label="accountOption(a)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('tradeSwitch.fTo')">
          <el-select v-model="nom.to" style="width: 100%">
            <el-option v-for="a in tradingAccounts" :key="a.id" :value="a.id" :label="accountOption(a)" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nom.visible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!nom.from || !nom.to || nom.from === nom.to" :loading="nom.loading"
                   @click="doNominate">{{ t('tradeSwitch.nominate') }}</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉：信息包五字段 + 检查单 -->
    <el-drawer v-model="detail.visible" :title="t('tradeSwitch.detTitle', { id: detail.row?.id })" size="52%">
      <template v-if="detail.data">
        <h4>{{ t('tradeSwitch.secOpenOrders') }}</h4>
        <el-alert type="warning" :title="t('tradeSwitch.caveatOpenOrders')" :closable="false"
                  style="margin-bottom: 8px" />
        <el-table :data="detail.data.info_pack.open_orders || []" size="small">
          <el-table-column prop="client_order_id" :label="t('tradeSwitch.colOrderId')" min-width="150" />
          <el-table-column prop="symbol" :label="t('tradeSwitch.colSymbol')" width="110" />
          <el-table-column prop="status" :label="t('tradeSwitch.colState')" width="100" />
        </el-table>

        <h4 style="margin-top: 16px">{{ t('tradeSwitch.secToState') }}</h4>
        <template v-if="detail.data.info_pack.to_account_state?.available">
          <p style="margin: 4px 0">{{ t('tradeSwitch.toTotalValue') }}: {{ detail.data.info_pack.to_account_state.total_value }}</p>
          <p style="margin: 4px 0">{{ t('tradeSwitch.toSnapshotAt') }}: {{ fmtTime(detail.data.info_pack.to_account_state.snapshot_ts) }}</p>
        </template>
        <el-alert v-else type="info" :title="t('tradeSwitch.toNoData')" :closable="false" />

        <h4 style="margin-top: 16px">{{ t('tradeSwitch.secPerms') }}</h4>
        <p style="margin: 4px 0">{{ t('tradeSwitch.permsToMarket') }}: {{ detail.data.info_pack.perms_reeval?.to_market }}（{{ t('tradeSwitch.permsOpKey') }}: {{ detail.data.info_pack.perms_reeval?.op_key || '—' }}）</p>

        <h4 style="margin-top: 16px">{{ t('tradeSwitch.secTasks') }}</h4>
        <el-table :data="detail.data.info_pack.affected_tasks || []" size="small">
          <el-table-column prop="id" label="#" width="70" />
          <el-table-column prop="name" :label="t('tradeSwitch.colTaskName')" min-width="140" />
          <el-table-column prop="status" :label="t('tradeSwitch.colTaskState')" width="110" />
        </el-table>

        <h4 style="margin-top: 16px">{{ t('tradeSwitch.secChecklist') }}</h4>
        <ul style="margin: 4px 0; padding-left: 18px; font-size: var(--fs-foot); line-height: 1.9">
          <li v-for="item in checklistItems" :key="item.key">
            <el-tag v-if="item.ok === true" type="success" size="small" style="margin-right: 6px">✓</el-tag>
            <el-tag v-else-if="item.ok === false" type="danger" size="small" style="margin-right: 6px">✗</el-tag>
            <el-tag v-else type="info" size="small" style="margin-right: 6px">{{ t('tradeSwitch.chkPending') }}</el-tag>
            {{ item.label }}
          </li>
        </ul>
      </template>
    </el-drawer>

    <!-- 执行确认（人工核实勾选） -->
    <el-dialog v-model="exec.visible" :title="t('tradeSwitch.execTitle')" width="460px">
      <el-alert type="warning" :title="t('tradeSwitch.execCaveat')" :closable="false" style="margin-bottom: 12px" />
      <el-checkbox v-model="exec.verified">{{ t('tradeSwitch.execVerify') }}</el-checkbox>
      <template #footer>
        <el-button @click="exec.visible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="warning" :disabled="!exec.verified" :loading="exec.loading"
                   @click="doExecute">{{ t('tradeSwitch.execute') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import TableShell from '../components/TableShell.vue'
import { apiErr } from '../api'
import {
  getTradeSwitchSessions, getTradeSwitchSession, nominateTradeSwitch,
  confirmTradeSwitch, extendTradeSwitch, executeTradeSwitch, abortTradeSwitch,
  getInterfaces,
} from '../api'

const { t } = useI18n()
const sessions = ref([])
const accounts = ref([])
const activeOnly = ref(false)
let timer = null

const nom = reactive({ visible: false, from: null, to: null, loading: false })
const detail = reactive({ visible: false, row: null, data: null })
const exec = reactive({ visible: false, row: null, verified: false, loading: false })

const tradingAccounts = () => accounts.value.filter(a => (a.capabilities || []).includes('trading'))

async function load() {
  try {
    const d = await getTradeSwitchSessions(activeOnly.value)
    sessions.value = d.items || []
  } catch (e) {
    ElMessage.error(apiErr(e, t('tradeSwitch.loadFail')))
  }
}

async function loadAccounts() {
  try {
    const d = await getInterfaces('trading')
    accounts.value = (d.items || d) || []
  } catch { /* 提名对话框打开时再提示 */ }
}

function accountLabel(id) {
  const a = accounts.value.find(x => x.id === id)
  return a ? `${a.name}（${a.provider}）` : `#${id}`
}
function accountOption(a) { return `${a.name}（${a.provider}·#${a.id}）` }

function stateTag(s) {
  return { nominated: 'primary', confirmed: 'warning', done: 'success',
           expired: 'info', aborted: 'danger' }[s] || 'info'
}
function stateText(s) { return t(`tradeSwitch.st_${s}`) }

function countdown(row) {
  const base = row.extended_at || row.nominated_at
  if (!base) return '—'
  const left = Math.floor((new Date(base).getTime() + (row.timeout_s || 300) * 1000 - Date.now()) / 1000)
  if (left <= 0) return t('tradeSwitch.expiredMark')
  const m = Math.floor(left / 60), s = left % 60
  return m > 0 ? t('tradeSwitch.countdownMs', { m, s }) : t('tradeSwitch.countdownS', { s })
}

// 检查单逐项渲染（文案师漏点②：禁原始 JSON 上屏——内部字段名映射为用户语言）
// 三态：true=过 / false=败 / undefined=未评估（execute 前——渲染「未评」非「失败」）
const checklistItems = computed(() => {
  const cl = detail.data?.checklist || {}
  const rc = cl.last_recheck || {}
  const failed = new Set(rc.failed || [])
  const evaluated = rc.pass !== undefined
  return [
    { key: 'open_orders_clear', ok: evaluated ? !failed.has('open_orders_clear') : undefined,
      label: t('tradeSwitch.itemOpenOrders') },
    { key: 'to_account_l1', ok: evaluated ? !failed.has('to_account_l1') : cl.cred_complete,
      label: t('tradeSwitch.itemToAccount') },
    { key: 'anchor_snapshot', ok: evaluated ? !failed.has('anchor_snapshot') : undefined,
      label: t('tradeSwitch.itemAnchor') },
    { key: 'no_running_tasks', ok: evaluated ? !failed.has('no_running_tasks') : undefined,
      label: t('tradeSwitch.itemNoRunning') },
    { key: 'from_still_referenced', ok: evaluated ? !failed.has('from_still_referenced') : undefined,
      label: t('tradeSwitch.itemFromRef') },
  ]
})

function fmtTime(v) {
  if (!v) return '—'
  try { return new Date(v).toLocaleString() } catch { return v }
}

function openNominate() {
  if (!accounts.value.length) loadAccounts()
  nom.from = nom.to = null
  nom.visible = true
}

async function doNominate() {
  nom.loading = true
  try {
    await nominateTradeSwitch({ from_account: nom.from, to_account: nom.to })
    ElMessage.success(t('tradeSwitch.nomOk'))
    nom.visible = false
    load()
  } catch (e) {
    ElMessage.error(apiErr(e, t('tradeSwitch.nomFail')))
  } finally { nom.loading = false }
}

async function openDetail(row) {
  detail.row = row
  detail.data = null
  detail.visible = true
  try { detail.data = await getTradeSwitchSession(row.id) }
  catch (e) { ElMessage.error(apiErr(e, t('tradeSwitch.loadFail'))) }
}

async function doConfirm(row) {
  try {
    const r = await confirmTradeSwitch(row.id)
    if (r.state === 'expired') {
      ElMessage.warning(t('tradeSwitch.confirmExpired'))
    } else {
      ElMessage.success(t('tradeSwitch.confirmOk'))
    }
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('tradeSwitch.confirmFail'))) }
}

async function doExtend(row) {
  try {
    await extendTradeSwitch(row.id)
    ElMessage.success(t('tradeSwitch.extendOk'))
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('tradeSwitch.extendFail'))) }
}

function openExecute(row) {
  exec.row = row
  exec.verified = false
  exec.visible = true
}

async function doExecute() {
  exec.loading = true
  try {
    const r = await executeTradeSwitch(exec.row.id, { manual_verified: exec.verified })
    if (r.executed) {
      ElMessage.success(t('tradeSwitch.execOk', { n: r.switched_tasks ?? 0 }))
      exec.visible = false
    } else {
      const failed = (r.recheck?.failed || []).map(f => t(`tradeSwitch.chk_${f}`)).join('；')
      ElMessage.warning(t('tradeSwitch.execBlocked', { failed }))    }
    load()
  } catch (e) {
    ElMessage.error(apiErr(e, t('tradeSwitch.execFail')))
  } finally { exec.loading = false }
}

async function doAbort(row) {
  try {
    const { value } = await ElMessageBox.prompt(t('tradeSwitch.abortAsk'), t('tradeSwitch.abort'), {
      confirmButtonText: t('common.ok'), cancelButtonText: t('common.cancel'),
      inputPlaceholder: t('tradeSwitch.abortPlaceholder'),
    })
    await abortTradeSwitch(row.id, { reason: value || '' })
    ElMessage.success(t('tradeSwitch.abortOk'))
    load()
  } catch (e) {
    if (e === 'cancel' || e?.action === 'cancel') return
    ElMessage.error(apiErr(e, t('tradeSwitch.abortFail')))
  }
}

onMounted(() => { load(); loadAccounts(); timer = setInterval(load, 30000) })
onUnmounted(() => clearInterval(timer))
</script>
