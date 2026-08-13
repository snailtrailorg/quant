<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('strategy.title') }}</span>
        <el-button type="primary" size="small" @click="openCreate">新建策略</el-button>
      </div>
    </template>
    <el-table :data="strategies" stripe>
      <el-table-column prop="name" :label="t('strategy.name')" />
      <el-table-column prop="type" :label="t('strategy.type')" />
      <el-table-column :label="t('strategy.status')">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '运行中' : '已停' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="success" @click="onStart(row.id)" v-if="!row.enabled">{{ t('strategy.start') }}</el-button>
          <el-button size="small" type="danger" @click="onStop(row.id)" v-if="row.enabled">{{ t('strategy.stop') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑策略" width="720px" :close-on-click-modal="false">
      <el-form :model="editForm" label-width="100px" v-loading="saving">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>
        <div style="color: #999; font-size: 12px; margin: -10px 0 10px 100px">
          策略不绑标的，标的由实盘任务/回测任务指定
        </div>

        <!-- 模式切换 -->
        <el-divider content-position="left">{{ t('strategy.mode') }}</el-divider>
        <el-form-item label="执行模式">
          <el-radio-group v-model="editForm.mode">
            <el-radio-button value="dsl">{{ t('strategy.dslMode') }}</el-radio-button>
            <el-radio-button value="python">{{ t('strategy.pythonMode') }}</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- DSL 模式 -->
        <template v-if="editForm.mode === 'dsl'">
          <el-divider content-position="left">因子配置</el-divider>
          <div v-for="(f, i) in editForm.factors" :key="i" style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center">
            <el-select v-model="f.name" placeholder="选择因子" style="width: 180px" @change="onFactorChange(f)">
              <el-option v-for="fac in availableFactors" :key="fac.name" :label="`${fac.name} (${fac.category})`" :value="fac.name" />
            </el-select>
            <el-input-number v-model="f.weight" :min="0" :max="2" :step="0.1" :precision="2" style="width: 120px" />
            <el-button size="small" type="danger" @click="removeFactor(i)">删</el-button>
          </div>
          <el-button size="small" @click="addFactor">+ 添加因子</el-button>

          <el-divider content-position="left">信号聚合</el-divider>
          <el-form-item label="买入阈值">
            <el-input-number v-model="editForm.aggregator.threshold_buy" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="卖出阈值">
            <el-input-number v-model="editForm.aggregator.threshold_sell" :step="0.1" :precision="2" />
          </el-form-item>

          <el-divider content-position="left">DSL 表达式（可选）</el-divider>
          <el-form-item label="表达式">
            <el-input v-model="editForm.dslExpr" type="textarea" :rows="3" :placeholder="t('strategy.dslHint')" />
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
                <el-button size="small" @click="validateCode" :loading="validating">{{ t('strategy.codeValidate') }}</el-button>
                <span v-if="codeValid === true" style="color: var(--el-color-success)">✅ {{ t('strategy.codeValid') }}</span>
                <span v-else-if="codeValid === false" style="color: var(--el-color-danger)">❌ {{ codeError }}</span>
              </div>
            </div>
          </el-form-item>
        </template>

        <!-- 参数定义 -->
        <el-divider content-position="left">参数定义（创建任务时动态生成表单）</el-divider>
        <div v-for="(pd, i) in editForm.parameterDefs" :key="i" style="margin-bottom: 12px; padding: 8px; border: 1px solid #eee; border-radius: 4px">
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
            <el-input v-model="pd.name" placeholder="参数名" style="width: 140px" />
            <el-select v-model="pd.type" placeholder="类型" style="width: 100px">
              <el-option label="数字" value="number" />
              <el-option label="布尔" value="boolean" />
              <el-option label="字符串" value="string" />
              <el-option label="选择" value="select" />
            </el-select>
            <el-input v-model="pd.label" placeholder="标签" style="width: 120px" />
            <el-input v-model="pd.default" placeholder="默认值" style="width: 100px" v-if="pd.type !== 'boolean'" />
            <el-switch v-model="pd.default" v-else />
            <template v-if="pd.type === 'number'">
              <el-input-number v-model="pd.min" placeholder="最小" style="width: 110px" :controls="false" />
              <el-input-number v-model="pd.max" placeholder="最大" style="width: 110px" :controls="false" />
              <el-input-number v-model="pd.step" placeholder="步长" style="width: 100px" :controls="false" />
            </template>
            <el-button size="small" type="danger" @click="editForm.parameterDefs.splice(i, 1)">删</el-button>
          </div>
          <el-input v-model="pd.description" placeholder="描述（可选）" style="margin-top: 6px" size="small" />
        </div>
        <el-button size="small" @click="addParamDef">+ 添加参数</el-button>

        <el-divider content-position="left">执行规则（ActionSignal）</el-divider>
        <el-form-item label="仓位类型">
          <el-select v-model="editForm.volumeType" style="width: 100%">
            <el-option label="股数（SHARES）" value="SHARES" />
            <el-option label="资金百分比（PERCENT）" value="PERCENT" />
            <el-option label="全仓（ALL_IN）" value="ALL_IN" />
          </el-select>
        </el-form-item>
        <el-form-item label="价格类型">
          <el-select v-model="editForm.priceType" style="width: 100%">
            <el-option label="限价（LIMIT）" value="LIMIT" />
            <el-option label="市价（MARKET）" value="MARKET" />
          </el-select>
        </el-form-item>
        <el-form-item label="有效期">
          <el-select v-model="editForm.orderValidity" style="width: 100%">
            <el-option label="当日（DAY）" value="DAY" />
            <el-option label="撤单前有效（GTC）" value="GTC" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">账户绑定（P2-2）</el-divider>
        <el-form-item label="绑定账户">
          <div style="display: flex; gap: 8px; align-items: center">
            <el-input v-model="bindForm.account_id" placeholder="账户 ID（如 253191001822）" style="width: 220px" />
            <el-select v-model="bindForm.broker_provider" style="width: 120px">
              <el-option label="XTP" value="xtp" />
              <el-option label="币安" value="binance" />
              <el-option label="OKX" value="okx" />
            </el-select>
            <el-input-number v-model="bindForm.initial_capital" :min="10000" :step="100000" style="width: 180px" />
            <el-button size="small" type="primary" @click="doBind" :loading="binding" :disabled="!editForm.id">绑定</el-button>
            <el-button size="small" @click="loadBinds" :disabled="!editForm.id">刷新</el-button>
          </div>
        </el-form-item>
        <el-table v-if="binds.length" :data="binds" stripe size="small" style="margin-bottom: 12px">
          <el-table-column prop="account_id" label="账户" />
          <el-table-column prop="broker_provider" label="通道" width="80" />
          <el-table-column prop="initial_capital" label="资金" width="120" />
          <el-table-column label="操作" width="80">
            <template #default="{ row }"><el-button size="small" type="danger" link @click="doUnbind(row.id)">解绑</el-button></template>
          </el-table-column>
        </el-table>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getStrategies, startStrategy, stopStrategy, updateStrategy, createStrategy, getFactorList, validatePythonCode } from '../api'
import api from '../api'
import PythonEditor from '../components/PythonEditor.vue'

const { t } = useI18n()
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
  factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 },
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
    ElMessage.success('绑定成功')
    await loadBinds()
  } catch { ElMessage.error('绑定失败') }
  finally { binding.value = false }
}
const doUnbind = async (id) => {
  try { await api.delete(`/strategy_account/${id}`); ElMessage.success('已解绑'); await loadBinds() } catch { ElMessage.error('解绑失败') }
}

const load = async () => { strategies.value = await getStrategies() }
const loadFactors = async () => { const r = await getFactorList(); availableFactors.value = r.items || [] }
const openCreate = () => {
  editForm.value = {
    id: '', name: '', enabled: true,
    mode: 'dsl',
    factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 },
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
      codeError.value = res.error || '校验失败'
      ElMessage.error(t('strategy.codeInvalid') + ': ' + (res.error || ''))
    }
  } catch (e) {
    codeValid.value = false
    codeError.value = e?.message || '校验请求失败'
    ElMessage.error('校验请求失败')
  }
  finally { validating.value = false }
}

const saveEdit = async () => {
  if (!editForm.value.id || !editForm.value.name) { ElMessage.warning('ID 和名称必填'); return }
  saving.value = true
  try {
    const params = {
      mode: editForm.value.mode,
      volume_type: editForm.value.volumeType,
      price_type: editForm.value.priceType,
      order_validity: editForm.value.orderValidity,
      parameter_defs: editForm.value.parameterDefs.filter(pd => pd.name),
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
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (e) { ElMessage.error('保存失败') }
  finally { saving.value = false }
}

const onStart = async id => { try { await startStrategy(id); ElMessage.success('已启动'); load() } catch (e) { console.error(e); ElMessage.error('启动失败') } }
const onStop = async id => { try { await stopStrategy(id); ElMessage.success('已停止'); load() } catch (e) { console.error(e); ElMessage.error('停止失败') } }
const removeFactor = (i) => { editForm.value.factors = editForm.value.factors.filter((_, idx) => idx !== i) }
onMounted(async () => { await load(); await loadFactors() })
</script>