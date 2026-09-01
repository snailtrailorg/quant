<template>
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>
            {{ t('backtest.runList') }}
            <!-- P2-4（05 §5.7）：badge 摘要 -->
            <el-tag size="small" style="margin-left: 8px">运行中 {{ runningCount }}</el-tag>
            <el-tag size="small" type="info" style="margin-left: 4px">今日 {{ todayCount }}</el-tag>
            <el-tag v-if="failedCount" size="small" type="danger" style="margin-left: 4px">失败 {{ failedCount }}</el-tag>
          </span>
          <span>
            <el-select v-model="filterStatus" size="small" clearable :placeholder="t('common.status')" style="width: 120px; margin-right: 8px">
              <el-option v-for="st in ['running','done','failed','pending']" :key="st" :value="st" :label="st" />
            </el-select>
            <el-button type="primary" @click="showForm = true" :disabled="navReadonly">{{ t('backtest.create') }}</el-button>
          </span>
        </div>
      </template>
      <el-table :data="filteredRuns" v-loading="loading" @row-click="goDetail">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="strategy_id" :label="t('backtest.strategy')" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ strategyName(row.strategy_id) }}</template>
        </el-table-column>
        <!-- P2-4：指标摘要——列表行直接给成绩，不用点进 Run 页 -->
        <el-table-column :label="t('backtest.retCol')" width="90" class-name="num">
          <template #default="{ row }">
            <span v-if="row.summary?.total_return != null" :class="row.summary.total_return >= 0 ? 'up' : 'down'">
              {{ (row.summary.total_return * 100).toFixed(1) }}%
            </span><span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.ddCol')" width="80" class-name="num">
          <template #default="{ row }">{{ row.summary?.max_drawdown != null ? (row.summary.max_drawdown * 100).toFixed(1) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('backtest.sharpeCol')" width="70" class-name="num">
          <template #default="{ row }">{{ row.summary?.sharpe_ratio?.toFixed(2) ?? '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('backtest.dateRangeCol')" width="160">
          <template #default="{ row }">
            {{ row.summary?.start?.slice(0,10) || (row.created_at||'').slice(0,10) }} ~ {{ row.summary?.end?.slice(0,10) || (row.finished_at||'').slice(5,10) }}
          </template>
        </el-table-column>
        <!-- 失败原因透出（05 §5.7：failed 行点开见原因） -->
        <el-table-column :label="t('backtest.reason')" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.status === 'failed'" style="color: var(--critical)">{{ row.summary?.error || row.error || '—' }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" :label="t('common.status')" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ enumZh(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="mode" :label="t('backtest.mode')" width="80" />
        <el-table-column :label="t('common.symbol')" min-width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ t('backtest.symbolCount', { n: row.symbols?.length || 0 }) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.createdAt')" width="160">
          <template #default="{ row }">{{ row.created_at?.slice(0, 19) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="160" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" @click.stop="goDetail(row)">{{ t('common.detail') }}</el-button>
            <el-button v-if="row.status === 'running'" type="danger" @click.stop="cancelRun(row)" :disabled="navReadonly">{{ t('backtest.terminate') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建回测弹窗 -->
    <el-dialog v-model="showForm" :title="t('backtest.create')" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item :label="t('backtest.strategy')">
          <el-select v-model="form.strategyId" :placeholder="t('backtest.phStrategy')" style="width: 100%" @change="onStrategyChange">
            <el-option v-for="st in strategies" :key="st.id" :value="st.id"
                       :label="`${st.name}${st.backtest_verified ? ' ✓' : '（未验证）'}`" :disabled="false" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.symbol')">
          <el-input v-model="form.symbolsStr" type="textarea" :rows="2"
            :placeholder="t('backtest.phSymbols')" />
        </el-form-item>
        <el-form-item :label="t('backtest.pool')">
          <el-select v-model="form.poolId" :placeholder="t('backtest.phPool')" clearable style="width: 100%">
            <el-option v-for="p in pools" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('backtest.dateRange')">
          <el-date-picker v-model="form.dateRange" type="daterange" :start-placeholder="t('common.startDate')" :end-placeholder="t('common.endDate')" style="width: 100%" />
          <div style="width: 100%; margin-top: 4px">
            <el-button v-for="q in quickRanges" :key="q.label" size="small" text type="primary" @click="form.dateRange = q.range()">{{ q.label }}</el-button>
          </div>
        </el-form-item>
        <el-form-item :label="t('backtest.commission')">
          <el-input-number v-model="form.commissionRate" :min="0" :step="0.0001" :precision="4" />
          <span style="margin-left: 8px; color: var(--text-secondary); font-size: var(--fs-foot)">万分之（默认 5=万5）</span>
        </el-form-item>
        <el-form-item :label="t('backtest.mode')">
          <el-select v-model="form.mode" style="width: 100%">
            <el-option :label="t('backtest.modeParallel')" value="parallel" />
            <el-option :label="t('backtest.modeSerial')" value="serial" />
            <el-option :label="t('backtest.modeSingle')" value="single" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.initialCapital')">
          <el-input-number v-model="form.capital" :min="10000" :step="100000" style="width: 100%" />
        </el-form-item>

        <!-- 统一参数 -->
        <el-divider content-position="left">{{ t('backtest.unifiedParams') }}</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: #999; font-size: 12px; padding-left: 100px">{{ t('backtest.noParams') }}</div>

        <!-- per-symbol 参数（高级） -->
        <el-divider content-position="left">
          <el-checkbox v-model="form.useSymbolParams">{{ t('backtest.advSymbolParams') }}</el-checkbox>
        </el-divider>
        <template v-if="form.useSymbolParams">
          <div style="color: #999; font-size: 12px; margin-bottom: 8px; padding-left: 100px">
            {{ t('backtest.jsonHint') }}
          </div>
          <el-input v-model="form.symbolParamsStr" type="textarea" :rows="4"
            placeholder='{"600000.SHSE": {"buy_threshold": 0.03}}' />
        </template>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="showForm = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="submitRun" :loading="submitting">{{ t('backtest.startRun') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBacktests, createBacktest, getStrategies, getPools } from '../api'
import api from '../api'
import { enumZh } from '../utils/format'
import ParameterForm from '../components/ParameterForm.vue'

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const router = useRouter()
const route = useRoute()
const runs = ref([])
const strategies = ref([])
const pools = ref([])
const loading = ref(false)
const submitting = ref(false)
const showForm = ref(false)
const parameterDefs = ref([])
const form = ref({
  strategyId: '', poolId: '', symbolsStr: '', dateRange: null, mode: 'parallel', capital: 1000000,
  params: {}, useSymbolParams: false, symbolParamsStr: '', commissionRate: 5,
})

const onStrategyChange = (sid) => {
  const s = strategies.value.find(x => x.id === sid)
  parameterDefs.value = s?.params?.parameter_defs || []
  form.value.params = {}
}

const statusType = (s) => ({ running: 'warning', done: 'success', error: 'danger', pending: 'info' }[s] || 'info')

const filterStatus = ref('')
const strategyName = (sid) => strategies.value.find(x => x.id === sid)?.name || sid
const runningCount = computed(() => runs.value.filter(r => r.status === 'running').length)
const todayCount = computed(() => runs.value.filter(r => (r.created_at || '').slice(0, 10) === new Date().toISOString().slice(0, 10)).length)
const failedCount = computed(() => runs.value.filter(r => r.status === 'failed').length)
const filteredRuns = computed(() => filterStatus.value ? runs.value.filter(r => r.status === filterStatus.value) : runs.value)
// P2-6：区间快捷项
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d }
const quickRanges = [
  { label: t('backtest.r1m'), range: () => [daysAgo(30), new Date()] },
  { label: t('backtest.r3m'), range: () => [daysAgo(90), new Date()] },
  { label: t('backtest.r6m'), range: () => [daysAgo(182), new Date()] },
  { label: t('backtest.r1y'), range: () => [daysAgo(365), new Date()] },
]
const loadRuns = async () => {
  loading.value = true
  try { runs.value = await getBacktests() } catch (e) { ElMessage.error(t('backtest.loadFailed')) }
  finally { loading.value = false }
}

const goDetail = (row) => router.push(`/backtest/${row.id}`)

const cancelRun = async (row) => {
  try {
    await ElMessageBox.confirm(t('backtest.confirmTerminate'), t('common.confirm'), { type: 'warning' })
    await api.post(`/tasks/${row.task_id}/terminate`)
    ElMessage.success(t('backtest.terminated'))
    await loadRuns()
  } catch (e) { ElMessage.error(t('backtest.terminateFailed')) }
}

const submitRun = async () => {
  if (!form.value.strategyId) { ElMessage.warning(t('backtest.selectStrategy')); return }
  submitting.value = true
  try {
    let symbols = []
    if (form.value.symbolsStr) {
      symbols = form.value.symbolsStr.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
    }
    let symbolParams = {}
    if (form.value.useSymbolParams && form.value.symbolParamsStr) {
      try { symbolParams = JSON.parse(form.value.symbolParamsStr) }
      catch { ElMessage.error(t('backtest.jsonError')); submitting.value = false; return }
    }
    const payload = {
      strategy_config_id: form.value.strategyId,
      symbols,
      pool_id: form.value.poolId || null,
      mode: form.value.mode,
      params: {
        capital: form.value.capital,
        commission: (form.value.commissionRate || 5) / 10000,   // A2:表单佣金(万分之)接入——原硬编码死控件
        start: form.value.dateRange?.[0]?.toISOString().slice(0, 10),
        end: form.value.dateRange?.[1]?.toISOString().slice(0, 10),
        ...form.value.params,
      },
      ...(Object.keys(symbolParams).length ? { symbol_params: symbolParams } : {}),
    }
    await createBacktest(payload)
    ElMessage.success(t('backtest.submitted'))
    showForm.value = false
    await loadRuns()
  } catch (e) { ElMessage.error(t('backtest.submitFailed')) }
  finally { submitting.value = false }
}

let pollTimer = null
onUnmounted(() => clearInterval(pollTimer))
onMounted(async () => {
  // P2-4：URL 预填（策略页"发起回测"深链 ?strategy=）
  const pre = route.query.strategy   // B-P1-5:history 路由,深链读 query
  if (pre) { showForm.value = true; form.value.strategyId = String(pre) }
  pollTimer = setInterval(() => { if (runs.value.some(r => r.status === 'running')) loadRuns() }, 5000)
  strategies.value = await getStrategies()
  try { pools.value = await getPools() } catch (e) {}
  await loadRuns()
})
</script>

