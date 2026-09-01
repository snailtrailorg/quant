<template>
  <el-card v-loading="saving">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('strategy.editTitle') }}</span>
        <el-button @click="$router.back()">{{ t('common.return') }}</el-button>
      </div>
    </template>
    <!-- 快照隔离横幅 -->
    <el-alert v-if="runningCount > 0" type="warning" :closable="false" style="margin-bottom: 12px">
      {{ t('strategy.snapshotIsolation', { n: runningCount }) }}
    </el-alert>
    <!-- 表单内容从弹窗提取 -->
      <el-alert v-if="runningTasksFor(editForm.id).length" type="warning" :closable="false" style="margin-bottom: 12px">
        {{ t('strategy.snapshotIsolation', { n: runningCount }) }}
      </el-alert>
    <el-form :model="editForm" label-width="100px" v-loading="saving">
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
    <div style="margin-top: 20px; display: flex; gap: 12px">
      <el-button type="primary" @click="save" :loading="saving">{{ t('common.save') }}</el-button>
      <el-button type="success" @click="saveAndBacktest" :loading="saving">{{ t('strategy.saveAndBacktest') }}</el-button>
    </div>
  </el-card>
</template>
<script setup>
// 独立编辑路由(05 §5.6:960px 抽屉装不下五节+DSL 编辑器——盲审三修正)
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const editForm = ref({ factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 }, needs_daily: true, needs_minute: false })
const saving = ref(false)
const runningCount = ref(0)

const load = async () => {
  const id = route.params.id
  const list = await api.get('/strategy')
  const st = (list || []).find(s => s.id === id)
  if (st) editForm.value = { ...st }
  try {
    const tasks = await api.get('/live-task')
    runningCount.value = (tasks || []).filter(t => t.strategy_id === id && t.status === 'running').length
  } catch {}
}
const save = async () => {
  saving.value = true
  try {
    await api.post(`/strategy/${editForm.value.id}`, editForm.value)
    router.push('/strategy')
  } finally { saving.value = false }
}
const saveAndBacktest = async () => {
  await save()
  router.push({ path: '/backtest', query: { strategy: editForm.value.id } })
}
onMounted(load)
</script>
