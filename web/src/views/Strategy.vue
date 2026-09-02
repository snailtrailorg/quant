<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('strategy.title') }}</span>
        <el-button type="primary" @click="!navReadonly && openCreate()" :disabled="navReadonly">{{ t('strategy.create') }}</el-button>
      </div>
    </template>
    <el-table :data="strategies">
      <el-table-column prop="name" :label="t('strategy.name')" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('strategy.type')" />
      <el-table-column :label="t('strategy.status')">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? t('strategy.statusRunning') : t('strategy.statusStopped') }}</el-tag>
        </template>
      </el-table-column>
      <!-- P2-1（05 §5.6）：验证✓独立成列（证据链可点）+最近回测列；操作列发起回测/编辑/复制/删除。
           链条打磨#22：策略无启停是设计（实盘启停唯一入口=LiveTask）；symbol:"" 是契约——勿修 -->
      <el-table-column :label="t('strategy.verifyCol')" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.backtest_verified" type="success" size="small" style="cursor:pointer"
                  @click="gotoVerifiedRun(row)">✓ {{ t('strategy.verified') }}</el-tag>
          <el-tag v-else type="info" size="small">{{ t('strategy.unverified') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('strategy.lastBtCol')" width="150">
        <template #default="{ row }">
          <span v-if="lastRun(row)" style="cursor:pointer" @click="$router.push(`/backtest/${lastRun(row).id}`)">
            <span :class="(bs(lastRun(row)).ret ?? 0) >= 0 ? 'up' : 'down'">
              {{ pct(bs(lastRun(row)).ret) }}
            </span>
            <span style="color: var(--text-secondary); font-size: var(--fs-foot)"> #{{ lastRun(row).id }}</span>
          </span>
          <span v-else style="color: var(--text-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="300" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" size="small" @click="runBacktest(row)" :disabled="navReadonly">{{ t('strategy.runBacktest') }}</el-button>
          <el-button size="small" @click="openEdit(row)" :disabled="navReadonly">{{ t('common.edit') }}</el-button>
          <el-button size="small" @click="onCopy(row)" :disabled="navReadonly">{{ t('common.copy') }}</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)" :disabled="navReadonly">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editVisible" :title="editForm.isNew ? t('strategy.createTitle') : t('strategy.editTitle')" width="720px" :close-on-click-modal="false">
      <!-- P2-2（05 §5.6 要点 4）：快照隔离横幅——改在跑策略不影响存量任务（快照固化） -->
      <el-alert v-if="runningTasksFor(editForm.id).length" type="warning" :closable="false" style="margin-bottom: 12px">
        {{ t('strategy.snapshotIsolation', { n: runningTasksFor(editForm.id).length }) }}
      </el-alert>
      <el-form :model="editForm" label-width="100px" v-loading="saving">
        <el-form-item label="ID">
          <el-input v-model="editForm.id" :placeholder="t('strategy.idHint')" :disabled="!editForm.isNew" />
        </el-form-item>
        <el-form-item :label="t('common.name')">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item :label="t('strategy.enable')">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>
        <div style="color: #999; font-size: 12px; margin: -10px 0 10px 100px">
          {{ t('strategy.hintNoSymbol') }}
        </div>

        <!-- 模式切换 -->
                <!-- P2-2(05 §5.6/16 号 §6):多频率 needs 声明段 -->
        <el-form-item :label="t('strategy.needsDecl')">
          <el-checkbox v-model="editForm.needs_daily" :label="t('strategy.needsDaily')" />
          <el-checkbox v-model="editForm.needs_minute" :label="t('strategy.needsMinute')" style="margin-left: 12px" />
        </el-form-item>
<el-divider content-position="left">{{ t('strategy.mode') }}</el-divider>
        <el-form-item :label="t('strategy.mode')">
          <el-radio-group v-model="editForm.mode">
            <el-radio-button value="dsl">{{ t('strategy.dslMode') }}</el-radio-button>
            <el-radio-button value="python">{{ t('strategy.pythonMode') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- DSL 模式 -->
        <template v-if="editForm.mode === 'dsl'">
          <el-divider content-position="left">{{ t('strategy.factorConfig') }}</el-divider>
          <div v-for="(f, i) in editForm.factors" :key="i" style="margin-bottom: 12px">
            <div style="display: flex; gap: 8px; align-items: center">
              <el-select v-model="f.name" :placeholder="t('strategy.phFactor')" style="width: 180px" @change="onFactorChange(f)">
                <el-option v-for="fac in availableFactors" :key="fac.name" :label="`${fac.name} (${fac.category})`" :value="fac.name" />
              </el-select>
              <el-input-number v-model="f.weight" :min="0" :max="2" :step="0.1" :precision="2" style="width: 120px" />
              <el-button type="danger" @click="removeFactor(i)">{{ t('strategy.removeFactor') }}</el-button>
            </div>
            <!-- 链条打磨#9：因子参数子表单（按因子 schema 动态展开——此前 params 锁死默认值改不了） -->
            <div v-if="factorSchema(f.name).length" style="display: flex; gap: 12px; margin: 6px 0 0 188px; flex-wrap: wrap">
              <div v-for="p in factorSchema(f.name)" :key="p.k" style="display: flex; align-items: center; gap: 4px">
                <span style="font-size: 12px; color: var(--el-text-color-secondary)">{{ p.k }}:</span>
                <el-input-number v-model="f.params[p.k]" :step="1" size="small" style="width: 110px" />
              </div>
            </div>
          </div>
          <el-button type="primary" @click="addFactor">{{ t('strategy.addFactor') }}</el-button>

          <el-divider content-position="left">{{ t('strategy.signalAgg') }}</el-divider>
          <el-form-item :label="t('strategy.thresholdBuy')">
            <el-input-number v-model="editForm.aggregator.threshold_buy" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item :label="t('strategy.thresholdSell')">
            <el-input-number v-model="editForm.aggregator.threshold_sell" :step="0.1" :precision="2" />
          </el-form-item>

          <el-divider content-position="left">{{ t('strategy.dslExprTitle') }}</el-divider>
          <el-form-item :label="t('strategy.expression')">
            <CodeEditor v-model="editForm.dslExpr" language="plaintext" :height="120" />
          </el-form-item>
        </template>

        <!-- Python 模式 -->
        <template v-if="editForm.mode === 'python'">
          <el-divider content-position="left">{{ t('strategy.pythonCode') }}</el-divider>
          <el-form-item>
            <div style="width: 100%">
              <div style="margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-secondary)">
                {{ t('strategy.pythonHint') }}
              </div>
              <PythonEditor v-model="editForm.pythonCode" :height="350" />
              <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center">
                <el-button type="primary" @click="validateCode" :loading="validating">{{ t('strategy.codeValidate') }}</el-button>
                <span v-if="codeValid === true" style="color: var(--el-color-success)">✅ {{ t('strategy.codeValid') }}</span>
                <span v-else-if="codeValid === false" style="color: var(--el-color-danger)">❌ {{ codeError }}</span>
              </div>
            </div>
          </el-form-item>
        </template>

        <!-- 参数定义 -->
        <el-divider content-position="left">{{ t('strategy.paramDef') }}</el-divider>
        <div v-for="(pd, i) in editForm.parameterDefs" :key="i" style="margin-bottom: 12px; padding: 8px; border: 1px solid #eee; border-radius: 4px">
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
            <el-input v-model="pd.name" :placeholder="t('strategy.phParamName')" style="width: 140px" />
            <el-select v-model="pd.type" :placeholder="t('common.type')" style="width: 100px">
              <el-option :label="t('strategy.optNumber')" value="number" />
              <el-option :label="t('strategy.optBoolean')" value="boolean" />
              <el-option :label="t('strategy.optString')" value="string" />
              <el-option :label="t('strategy.optSelect')" value="select" />
            </el-select>
            <el-input v-model="pd.label" :placeholder="t('strategy.phLabel')" style="width: 120px" />
            <el-input v-model="pd.default" :placeholder="t('strategy.phDefault')" style="width: 100px" v-if="pd.type !== 'boolean'" />
            <el-switch v-model="pd.default" v-else />
            <template v-if="pd.type === 'number'">
              <el-input-number v-model="pd.min" :placeholder="t('strategy.phMin')" style="width: 110px" :controls="false" />
              <el-input-number v-model="pd.max" :placeholder="t('strategy.phMax')" style="width: 110px" :controls="false" />
              <el-input-number v-model="pd.step" :placeholder="t('strategy.phStep')" style="width: 100px" :controls="false" />
            </template>
            <el-button type="danger" @click="editForm.parameterDefs.splice(i, 1)">{{ t('common.delete') }}</el-button>
          </div>
          <el-input v-model="pd.description" :placeholder="t('strategy.phDesc')" style="margin-top: 6px" />
        </div>
        <el-button type="primary" @click="addParamDef">{{ t('strategy.addParam') }}</el-button>

        <el-divider content-position="left">{{ t('strategy.execRule') }}</el-divider>
        <el-form-item :label="t('strategy.volumeType')">
          <el-select v-model="editForm.volumeType" style="width: 100%">
            <el-option :label="t('strategy.optShares')" value="SHARES" />
            <el-option :label="t('strategy.optPercent')" value="PERCENT" />
            <el-option :label="t('strategy.optAllIn')" value="ALL_IN" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('strategy.priceType')">
          <el-select v-model="editForm.priceType" style="width: 100%">
            <el-option :label="t('strategy.optLimit')" value="LIMIT" />
            <el-option :label="t('strategy.optMarket')" value="MARKET" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('strategy.validity')">
          <el-select v-model="editForm.orderValidity" style="width: 100%">
            <el-option :label="t('strategy.optDay')" value="DAY" />
            <el-option :label="t('strategy.optGtc')" value="GTC" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">{{ t('strategy.accountBind') }}</el-divider>
        <el-form-item :label="t('strategy.bindAccount')">
          <div style="display: flex; gap: 8px; align-items: center">
            <el-input v-model="bindForm.account_id" :placeholder="t('strategy.phAccountId')" style="width: 220px" />
            <el-select v-model="bindForm.broker_provider" style="width: 120px">
              <el-option label="XTP" value="xtp" />
              <el-option :label="t('common.binance')" value="binance" />
              <el-option label="OKX" value="okx" />
            </el-select>
            <el-input-number v-model="bindForm.initial_capital" :min="10000" :step="100000" style="width: 180px" />
            <el-button type="primary" @click="doBind" :loading="binding" :disabled="!editForm.id">{{ t('common.bind') }}</el-button>
            <el-button type="primary" @click="loadBinds" :disabled="!editForm.id">{{ t('common.refresh') }}</el-button>
          </div>
        </el-form-item>
        <el-table v-if="binds.length" :data="binds" style="margin-bottom: 12px">
          <el-table-column prop="account_id" :label="t('common.account')" show-overflow-tooltip />
          <el-table-column prop="broker_provider" :label="t('common.broker')" width="80" />
          <el-table-column prop="initial_capital" :label="t('strategy.colCapital')" width="120" />
          <el-table-column :label="t('common.action')" width="80">
            <template #default="{ row }"><el-button type="danger" @click="doUnbind(row.id)">{{ t('common.unbind') }}</el-button></template>
          </el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="editVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving">{{ t('common.save') }}</el-button>
        <el-button type="success" @click="saveAndBacktest" :loading="saving">{{ t('strategy.saveAndBacktest') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
const router = useRouter()
import { ElMessage, ElMessageBox } from 'element-plus'
import { bs, pct } from '../utils/backtestSummary'
import { getStrategies, updateStrategy, createStrategy, getFactorList, validatePythonCode } from '../api'
import api from '../api'
import PythonEditor from '../components/PythonEditor.vue'
import CodeEditor from '../components/CodeEditor.vue'

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const strategies = ref([])
const availableFactors = ref([])
const editVisible = ref(false)
const saving = ref(false)
const validating = ref(false)
const codeValid = ref(null)  // null=未校验, true=通过, false=失败
const codeError = ref('')

const DEFAULT_PYTHON_TEMPLATE = `def on_bar(ctx):
    """策略逻辑入口。每根 K 线回调一次。

    ctx 可用方法：
      ctx.get_bar(field)      — 取当前 bar 字段值（close/open/high/low/volume）
      ctx.get_history(n)       — 取最近 n 根 bar 的 close 列表
      ctx.get_full_history(n)  — 取最近 n 根 bar 的完整 dict 列表
      ctx.get_param(key)       — 取策略参数
      ctx.buy(volume)          — 买入信号
      ctx.sell(volume)         — 卖出信号
      ctx.hold()               — 持仓不动
      ctx.set_state(k, v)      — 保存运行时状态
      ctx.get_state(k, d)      — 读取运行时状态
    """
    close = ctx.get_bar("close")
    hist = ctx.get_history(20)
    if len(hist) >= 20:
        sma = sum(hist) / len(hist)
        if close > sma * 1.02:
            return ctx.buy(100)
        elif close < sma * 0.98:
            return ctx.sell(100)
    return ctx.hold()
`

const editForm = ref({
  id: '', name: '', enabled: true,
  mode: 'dsl',
  factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 }, needs_daily: true, needs_minute: false,
  dslExpr: '', pythonCode: DEFAULT_PYTHON_TEMPLATE,
  volumeType: 'SHARES', priceType: 'LIMIT', orderValidity: 'DAY',
  parameterDefs: [],
})
const binds = ref([])
const binding = ref(false)
const bindForm = ref({ account_id: '', broker_provider: 'xtp', initial_capital: 1000000 })

const addParamDef = () => {
  editForm.value.parameterDefs.push({
    name: '', type: 'number', label: '', default: 0,
    min: undefined, max: undefined, step: undefined, description: '',
  })
}

const loadBinds = async () => {
  if (!editForm.value.id) return
  try { binds.value = await api.get('/strategy_account', { params: { strategy_id: editForm.value.id } }) } catch { binds.value = [] }
}
const doBind = async () => {
  if (!bindForm.value.account_id) return
  binding.value = true
  try {
    await api.post('/strategy_account', { ...bindForm.value, strategy_id: editForm.value.id })
    ElMessage.success(t('common.bindSuccess'))
    await loadBinds()
  } catch { ElMessage.error(t('common.bindFailed')) }
  finally { binding.value = false }
}
const doUnbind = async (id) => {
  try { await api.delete(`/strategy_account/${id}`); ElMessage.success(t('common.unbindSuccess')); await loadBinds() } catch { ElMessage.error(t('common.unbindFailed')) }
}

const load = async () => { strategies.value = await getStrategies() }

// P2-1：最近回测映射 + 证据链跳转 + 一键回测 + 复制/删除
const backtestRuns = ref([])
const liveTasksAll = ref([])
const loadExtra = async () => {
  try { backtestRuns.value = await api.get('/backtest') } catch { backtestRuns.value = [] }
  try { liveTasksAll.value = await api.get('/live-task') } catch { liveTasksAll.value = [] }
}
const lastRun = (row) => {
  const runs = backtestRuns.value.filter(b => b.strategy_config_id === row.id && b.status === 'done')
  return runs[0] || null
}
const runningTasksFor = (sid) => liveTasksAll.value.filter(x => x.strategy_id === sid && x.status === 'running')
const gotoVerifiedRun = (row) => { const r = lastRun(row); if (r) router.push(`/backtest/${r.id}`) }
const runBacktest = (row) => { router.push({ path: '/backtest', query: { strategy: row.id } }) }
const onCopy = async (row) => {
  try {
    const copy = { ...row, id: row.id + '_copy', name: row.name + ' (副本)' }
    delete copy.backtest_verified
    await createStrategy(copy); ElMessage.success(t('common.success')); load()
  } catch { ElMessage.error(t('common.failed')) }
}
const onDelete = async (row) => {
  try {
    const n = runningTasksFor(row.id).length
    await ElMessageBox.confirm(n ? t('strategy.deleteBlocked', { n }) : t('strategy.confirmDeleteName', { name: row.name }),
                               t('common.confirm'), { type: 'warning' })
    await api.delete(`/strategy/${row.id}`); ElMessage.success(t('common.success')); load()
  } catch (e) { if (e?.response) ElMessage.error(t('common.failed')) }
}
const saveAndBacktest = async () => {
  await saveEdit()
  if (editForm.value.id) router.push({ path: '/backtest', query: { strategy: editForm.value.id } })
}
const loadFactors = async () => { const r = await getFactorList(); availableFactors.value = r.items || [] }
const openCreate = () => {
  editForm.value = {
    id: '', name: '', enabled: true,
    mode: 'dsl',
    factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 }, needs_daily: true, needs_minute: false,
    dslExpr: '', pythonCode: DEFAULT_PYTHON_TEMPLATE,
    volumeType: 'SHARES', priceType: 'LIMIT', orderValidity: 'DAY',
    parameterDefs: [],
    isNew: true,
  }
  codeValid.value = null
  codeError.value = ''
  editVisible.value = true
}

const openEdit = (row) => {
  editForm.value = {
    id: row.id,
    name: row.name,
    enabled: row.enabled,
    mode: row.params?.mode || 'dsl',
    factors: (row.factors || []).map(f => ({ name: f.name, weight: f.weight, params: f.params || {} })),
    aggregator: { ...row.aggregator } || { threshold_buy: 0.3, threshold_sell: -0.3 },
    dslExpr: row.params?.dsl_expr || '',
    pythonCode: row.params?.python_code || DEFAULT_PYTHON_TEMPLATE,
    volumeType: row.params?.volume_type || 'SHARES',
    priceType: row.params?.price_type || 'LIMIT',
    orderValidity: row.params?.order_validity || 'DAY',
    parameterDefs: row.params?.parameter_defs || [],
    needs_daily: row.params?.needs_daily ?? true,
    needs_minute: row.params?.needs_minute ?? false,
    isNew: false,
  }
  codeValid.value = null
  codeError.value = ''
  editVisible.value = true
}

const addFactor = () => { editForm.value.factors.push({ name: '', weight: 0.5, params: {} }) }
const onFactorChange = (f) => {
  const fac = availableFactors.value.find(x => x.name === f.name)
  if (fac) f.params = { ...fac.params }
}
// #9：因子参数 schema（数值型 params 展开为可编辑项）
const factorSchema = (name) => {
  const fac = availableFactors.value.find(x => x.name === name)
  if (!fac || !fac.params) return []
  return Object.entries(fac.params)
    .filter(([, v]) => typeof v === 'number')
    .map(([k, v]) => ({ k, default: v }))
}

const validateCode = async () => {
  validating.value = true
  codeValid.value = null
  codeError.value = ''
  try {
    const res = await validatePythonCode(editForm.value.pythonCode)
    if (res.valid) {
      codeValid.value = true
      ElMessage.success(t('strategy.codeValid'))
    } else {
      codeValid.value = false
      codeError.value = res.error || t('strategy.validateFailed')
      ElMessage.error(t('strategy.codeInvalid') + ': ' + (res.error || ''))
    }
  } catch (e) {
    codeValid.value = false
    codeError.value = e?.message || t('strategy.validateReqFailed')
    ElMessage.error(t('strategy.validateReqFailed'))
  }
  finally { validating.value = false }
}

const saveEdit = async () => {
  if (!editForm.value.id || !editForm.value.name) { ElMessage.warning(t('strategy.idNameRequired')); return }
  saving.value = true
  try {
    const params = {
      mode: editForm.value.mode,
      volume_type: editForm.value.volumeType,
      price_type: editForm.value.priceType,
      order_validity: editForm.value.orderValidity,
      parameter_defs: editForm.value.parameterDefs.filter(pd => pd.name),
      needs_daily: editForm.value.needs_daily,      // wd-16 数据需求声明（后端前瞻消费）
      needs_minute: editForm.value.needs_minute,
    }
    if (editForm.value.mode === 'dsl') {
      if (editForm.value.dslExpr) params.dsl_expr = editForm.value.dslExpr
    } else {
      params.python_code = editForm.value.pythonCode
    }
    const payload = {
      name: editForm.value.name,
      symbol: '',
      enabled: editForm.value.enabled,
      factors: editForm.value.mode === 'dsl' ? editForm.value.factors.filter(f => f.name) : [],
      aggregator: editForm.value.aggregator,
      params,
    }
    if (editForm.value.isNew) {
      await createStrategy({ ...payload, id: editForm.value.id, type: 'astock_analysis', adapter: 'xtp', risk: {} })
    } else {
      await updateStrategy(editForm.value.id, payload)
    }
    ElMessage.success(t('common.saveSuccess'))
    editVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.saveFailed')) }
  finally { saving.value = false }
}

const removeFactor = (i) => { editForm.value.factors = editForm.value.factors.filter((_, idx) => idx !== i) }
onMounted(async () => {
  loadExtra()
  await load(); await loadFactors() })
</script>