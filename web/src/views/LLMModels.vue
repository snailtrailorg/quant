<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('llm.configTitle') }}<el-button type="primary" @click="onAdd">{{ t('common.create') }}</el-button></div></template>
    <el-card shadow="never" style="margin-bottom: 12px">
      <template #header>{{ t('llm.usageTitle') }}<el-button type="primary" @click="loadUsage" style="margin-left: var(--sp-2)">{{ t('common.refresh') }}</el-button></template>
      <el-table :data="usage.month">
        <el-table-column prop="provider" label="Provider" min-width="120" />
        <el-table-column prop="model" :label="t('llm.model')" min-width="200" show-overflow-tooltip />
        <el-table-column prop="calls" :label="t('llm.calls')" min-width="80" />
        <el-table-column :label="t('llm.tokenCol')" min-width="160">
          <template #default="{ row }">{{ row.input_tokens.toLocaleString() }} / {{ row.output_tokens.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="avg_latency_ms" :label="t('llm.latencyMs')" min-width="100" />
        <el-table-column :label="t('llm.successRateCol')" min-width="100">
          <template #default="{ row }"><el-tag :type="row.success_rate >= 95 ? 'success' : 'warning'">{{ row.success_rate }}%</el-tag></template>
        </el-table-column>
      </el-table>
      <div style="font-size: 12px; color: var(--text-secondary); margin-top: var(--sp-2)">
        {{ t('llm.trend7d') }}<span v-for="tr in usage.trend" :key="tr.date" style="margin-right: 10px">{{ tr.date.slice(5) }} {{tr.calls}}/{{tr.total_tokens.toLocaleString()}}tk</span><span v-if="!usage.trend.length">{{ t('llm.noTrend') }}</span>
      </div>
    </el-card>
    <el-table :data="models">
      <el-table-column prop="id" label="ID" min-width="80" />
      <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip />
      <el-table-column prop="provider" label="Provider" min-width="120" />
      <el-table-column prop="model" :label="t('llm.model')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="base_url" :label="t('cols.apiUrl')" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.base_url || '-' }}</template>
      </el-table-column>
      <el-table-column prop="context_window" :label="t('cols.contextWindow')" min-width="110">
        <template #default="{ row }">{{ row.context_window?.toLocaleString() || '-' }}</template>
      </el-table-column>
      <el-table-column :label="t('llm.key')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.has_key ? 'success' : 'info'">{{ row.has_key ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="priority" :label="t('llm.priority')" min-width="80" />
      <el-table-column :label="t('common.enable')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="250">
        <template #default="{ row }">
          <div style="display: inline-flex; gap: 6px; align-items: center; white-space: nowrap">
            <el-button type="primary" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
            <el-button type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
            <el-button type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('llm.editModel') : t('llm.addModel')" width="560px">
      <el-form :model="form" label-width="120px">
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('llm.phProvider')" /></el-form-item>
      <el-form-item :label="t('llm.model')"><el-input v-model="form.model" /></el-form-item>
      <el-form-item :label="t('llm.apiKey')"><el-input v-model="form.api_key" type="password" show-password :placeholder="t('common.phEditNoChange')" autocomplete="new-password" /></el-form-item>
      <el-form-item :label="t('llm.baseUrl')"><el-input v-model="form.base_url" /></el-form-item>
      <el-form-item :label="t('cols.contextWindow')"><el-input-number v-model="form.context_window" :min="0" :step="1024" controls-position="right" /></el-form-item>
      <el-form-item :label="t('llm.maxInputTokens')"><el-input-number v-model="form.max_input_tokens" :min="0" controls-position="right" :placeholder="t('llm.phInputTokens')" /></el-form-item>
      <el-form-item :label="t('llm.maxOutputTokens')"><el-input-number v-model="form.max_output_tokens" :min="0" controls-position="right" :placeholder="t('llm.phOutputTokens')" /></el-form-item>
      <el-form-item :label="t('llm.priority')"><el-input-number v-model="form.priority" :min="1" :max="100" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
      </template>
    </el-dialog>
  </el-card>

  <!-- P2-3 LLM 预算预警 -->
  <el-card style="margin-top: 20px" v-loading="budgetLoading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('llm.budgetTitle') }}</span>
        <el-button type="primary" @click="checkBudget" :loading="checking">{{ t('llm.check') }}</el-button>
      </div>
    </template>
    <el-table :data="budgets">
      <el-table-column prop="provider" label="Provider" min-width="120"><template #default="{ row }">{{ row.provider || t('llm.global') }}</template></el-table-column>
      <el-table-column prop="daily_token_limit" :label="t('llm.dailyTokenLimit')" min-width="120">
        <template #default="{ row }">{{ row.daily_token_limit?.toLocaleString() || '-' }}</template>
      </el-table-column>
      <el-table-column prop="monthly_cost_limit" :label="t('cols.monthlyCostLimit')" min-width="120">
        <template #default="{ row }">{{ row.monthly_cost_limit != null ? `¥${row.monthly_cost_limit}` : '-' }}</template>
      </el-table-column>
      <el-table-column prop="alert_threshold_pct" :label="t('llm.alertThreshold')" min-width="110" />
      <el-table-column prop="enabled" :label="t('common.enable')" min-width="80"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template></el-table-column>
      <el-table-column prop="updated_at" :label="t('common.updatedAt')" min-width="160"><template #default="{ row }">{{ row.updated_at ? fmtTime.full(row.updated_at) : '-' }}</template></el-table-column>
      <el-table-column v-if="role === 'admin'" :label="t('common.action')" width="110">
        <template #default="{ row }">
          <el-button type="primary" @click="onBudgetEdit(row)">{{ t('common.edit') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <!-- 预算编辑（批16：后端本有 POST /llm-budget/{bid}，前端补入口） -->
    <el-dialog v-model="budgetDlg" :close-on-click-modal="false" :title="t('llm.budgetEdit')" width="480px">
      <el-form :model="budgetForm" label-width="130px">
        <el-form-item label="Provider"><el-input :model-value="budgetForm.provider || t('llm.global')" disabled /></el-form-item>
        <el-form-item :label="t('llm.dailyTokenLimit')"><el-input-number v-model="budgetForm.daily_token_limit" :min="0" :step="10000" controls-position="right" style="width: 100%" /></el-form-item>
        <el-form-item :label="t('cols.monthlyCostLimit')"><el-input-number v-model="budgetForm.monthly_cost_limit" :min="0" :step="100" controls-position="right" style="width: 100%" /></el-form-item>
        <el-form-item :label="t('llm.alertThreshold')"><el-input-number v-model="budgetForm.alert_threshold_pct" :min="1" :max="100" controls-position="right" style="width: 100%" /></el-form-item>
        <el-form-item :label="t('common.enable')"><el-switch v-model="budgetForm.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="budgetDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveBudget" :loading="budgetSaving">{{ t('common.update') }}</el-button>
      </template>
    </el-dialog>
    <el-alert v-if="budgetCheck" :type="budgetCheck.alerts?.length ? 'warning' : 'success'" :closable="false" style="margin-top: 12px">
      {{ budgetCheck.alerts?.length ? t('llm.alertsOver', { n: budgetCheck.alerts.length }) : t('llm.alertsOk', { n: budgetCheck.checked }) }}
    </el-alert>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getLLMModels, createLLMModel, updateLLMModel, deleteLLMModel, testLLMModel, getLLMUsage } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fmtTime } from '../utils/fmtTime'
import api from '../api'

const { t } = useI18n()
const models = ref([])
const usage = ref({ today: [], month: [], trend: [] })
const budgets = ref([])
const budgetLoading = ref(false)
const budgetCheck = ref(null)
const checking = ref(false)
const role = ref(localStorage.getItem('role') || 'viewer')

const loadBudget = async () => { budgetLoading.value = true; try { budgets.value = await api.get('/llm-budget') } catch {} finally { budgetLoading.value = false } }
const checkBudget = async () => { checking.value = true; try { budgetCheck.value = await api.post('/llm-budget/check') } catch { ElMessage.error(t('llm.checkFailed')) } finally { checking.value = false } }

// 预算编辑（批16）
const budgetDlg = ref(false)
const budgetSaving = ref(false)
const budgetForm = ref({})
const onBudgetEdit = (row) => { budgetForm.value = { ...row }; budgetDlg.value = true }
const saveBudget = async () => {
  budgetSaving.value = true
  try {
    await api.post(`/llm-budget/${budgetForm.value.id}`, budgetForm.value)
    ElMessage.success(t('common.saveSuccess'))
    budgetDlg.value = false
    loadBudget()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { budgetSaving.value = false }
}
const form = ref(emptyForm())
const saving = ref(false)
const dlg = ref(false)   // 编辑形态弹窗化（DESIGN 新立法）
const testing = ref(0)

function emptyForm() {
  return { name: '', provider: '', model: '', api_key: '', base_url: '', priority: 10, enabled: false, context_window: 32768, supports_tools: true, max_input_tokens: null, max_output_tokens: null, temperature: null }
}

const load = async () => { try { models.value = await getLLMModels() } catch (e) { console.error(e) } }
const loadUsage = async () => { try { usage.value = await getLLMUsage() } catch (e) { console.error(e) } }
onMounted(() => { load(); loadUsage(); loadBudget() })

const onEdit = (row) => { form.value = { ...row, api_key: '' } ; dlg.value = true }
const resetForm = () => { form.value = emptyForm() }
const onAdd = () => { resetForm(); dlg.value = true }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateLLMModel(form.value.id, form.value)
    else await createLLMModel(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteLLMModel(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testLLMModel(id)
    if (r.ok) ElMessage.success(t('llm.connectOkReply', { reply: r.reply || '' }))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}
</script>
