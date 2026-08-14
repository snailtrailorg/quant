<template>
  <el-card>
    <template #header>{{ t('llm.configTitle') }}</template>
    <el-card shadow="never" style="margin-bottom: 12px">
      <template #header>{{ t('llm.usageTitle') }}<el-button @click="loadUsage" size="small" style="margin-left: 8px">{{ t('common.refresh') }}</el-button></template>
      <el-table :data="usage.month" stripe size="small">
        <el-table-column prop="provider" label="Provider" width="120" />
        <el-table-column prop="model" :label="t('llm.model')" />
        <el-table-column prop="calls" :label="t('llm.calls')" width="80" />
        <el-table-column :label="t('llm.tokenCol')" width="160">
          <template #default="{ row }">{{ row.input_tokens.toLocaleString() }} / {{ row.output_tokens.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="avg_latency_ms" :label="t('llm.latencyMs')" width="80" />
        <el-table-column :label="t('llm.successRateCol')" width="80">
          <template #default="{ row }"><el-tag :type="row.success_rate >= 95 ? 'success' : 'warning'" size="small">{{ row.success_rate }}%</el-tag></template>
        </el-table-column>
      </el-table>
      <div style="font-size: 12px; color: #999; margin-top: 8px">
        {{ t('llm.trend7d') }}<span v-for="tr in usage.trend" :key="tr.date" style="margin-right: 10px">{{ tr.date.slice(5) }} {{tr.calls}}/{{tr.total_tokens.toLocaleString()}}tk</span><span v-if="!usage.trend.length">{{ t('llm.noTrend') }}</span>
      </div>
    </el-card>
    <el-table :data="models" stripe size="small">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" :label="t('common.name')" />
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="model" :label="t('llm.model')" />
      <el-table-column :label="t('llm.key')" width="80">
        <template #default="{ row }"><el-tag :type="row.has_key ? 'success' : 'info'" size="small">{{ row.has_key ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="priority" :label="t('llm.priority')" width="80" />
      <el-table-column :label="t('common.enable')" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button size="small" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button size="small" type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? t('llm.editModel') : t('llm.addModel') }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('llm.phProvider')" /></el-form-item>
      <el-form-item :label="t('llm.model')"><el-input v-model="form.model" /></el-form-item>
      <el-form-item :label="t('llm.apiKey')"><el-input v-model="form.api_key" type="password" show-password :placeholder="t('common.phEditNoChange')" /></el-form-item>
      <el-form-item :label="t('llm.baseUrl')"><el-input v-model="form.base_url" /></el-form-item>
      <el-form-item :label="t('llm.maxInputTokens')"><el-input-number v-model="form.max_input_tokens" :min="0" controls-position="right" :placeholder="t('llm.phInputTokens')" /></el-form-item>
      <el-form-item :label="t('llm.maxOutputTokens')"><el-input-number v-model="form.max_output_tokens" :min="0" controls-position="right" :placeholder="t('llm.phOutputTokens')" /></el-form-item>
      <el-form-item :label="t('llm.priority')"><el-input-number v-model="form.priority" :min="1" :max="100" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button size="small" type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        <el-button size="small" @click="resetForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </el-card>

  <!-- P2-3 LLM 预算预警 -->
  <el-card style="margin-top: 20px" v-loading="budgetLoading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('llm.budgetTitle') }}</span>
        <el-button size="small" @click="checkBudget" :loading="checking">{{ t('llm.check') }}</el-button>
      </div>
    </template>
    <el-table :data="budgets" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120"><template #default="{ row }">{{ row.provider || t('llm.global') }}</template></el-table-column>
      <el-table-column prop="daily_token_limit" :label="t('llm.dailyTokenLimit')" width="120" />
      <el-table-column prop="alert_threshold_pct" :label="t('llm.alertThreshold')" width="100" />
      <el-table-column prop="enabled" :label="t('common.enable')" width="80"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '✓' : '✗' }}</el-tag></template></el-table-column>
      <el-table-column prop="updated_at" :label="t('common.updatedAt')"><template #default="{ row }">{{ row.updated_at?.slice(0,19) || '-' }}</template></el-table-column>
    </el-table>
    <el-alert v-if="budgetCheck" :type="budgetCheck.alerts?.length ? 'warning' : 'success'" :closable="false" style="margin-top: 12px">
      {{ budgetCheck.alerts?.length ? t('llm.alertsOver', { n: budgetCheck.alerts.length }) : t('llm.alertsOk', { n: budgetCheck.checked }) }}
    </el-alert>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getLLMModels, createLLMModel, updateLLMModel, deleteLLMModel, testLLMModel, getLLMUsage } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const models = ref([])
const usage = ref({ today: [], month: [], trend: [] })
const budgets = ref([])
const budgetLoading = ref(false)
const budgetCheck = ref(null)
const checking = ref(false)

const loadBudget = async () => { budgetLoading.value = true; try { budgets.value = await api.get('/llm-budget') } catch {} finally { budgetLoading.value = false } }
const checkBudget = async () => { checking.value = true; try { budgetCheck.value = await api.post('/llm-budget/check') } catch { ElMessage.error(t('llm.checkFailed')) } finally { checking.value = false } }
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { name: '', provider: '', model: '', api_key: '', base_url: '', priority: 10, enabled: false, context_window: 32768, supports_tools: true, max_input_tokens: null, max_output_tokens: null, temperature: null }
}

const load = async () => { try { models.value = await getLLMModels() } catch (e) { console.error(e) } }
const loadUsage = async () => { try { usage.value = await getLLMUsage() } catch (e) { console.error(e) } }
onMounted(() => { load(); loadUsage(); loadBudget() })

const onEdit = (row) => { form.value = { ...row, api_key: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateLLMModel(form.value.id, form.value)
    else await createLLMModel(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || t('common.saveFailed')) }
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
