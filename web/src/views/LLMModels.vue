<template>
  <!-- 批50 重构：两 section 平铺——模型列表（上，拖拽行序=容灾链序）+用量监控（下，卡片曲线）。
       原预算预警卡彻底退役（用户裁定 B：beat 任务/端点/表 0088 随版 DROP）。
       原内嵌"用量监控"子卡（当日表+7 日文本行）退役→独立 section 卡片化。 -->
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          {{ t('llm.configTitle') }}
          <div style="display: flex; gap: 8px; align-items: center">
            <ColumnSettings storage-key="cols.llm-models" :columns="modelColDefs" v-model:visible="modelVisible" />
            <IconBtn :icon="Plus" :title="t('common.create')" @click="onAdd" />
          </div>
        </div>
      </template>
      <div class="order-note">{{ t('llm.llmListOrderNote') }} <span class="cooldown-note">{{ t('llm.llmCooldownNote') }}</span></div>
      <TableShell :data="models" storage-key="llm-models" row-key="id">
        <el-table-column :label="t('alerts.smtpProvDrag')" width="46">   <!-- 批46 形态：menu 列头+三横线手柄；:label 保留供列宽键 -->
          <template #header><el-icon :title="t('llm.llmListOrderNote')" :size="16"><Menu /></el-icon></template>
          <template #default="{ $index }">
            <span class="drag-handle" :draggable="true"
                  :title="t('alerts.smtpProvDrag')"
                  @dragstart="onDragStart($index)" />
          </template>
        </el-table-column>
        <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip>
          <template #default="{ row, $index }">
            <span @dragover.prevent @drop="onDrop($index)">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="modelColOn('provider')" prop="provider" :label="t('llm.providerCol')" min-width="120" />
        <el-table-column v-if="modelColOn('model')" prop="model" :label="t('llm.model')" min-width="200" show-overflow-tooltip />
        <el-table-column v-if="modelColOn('enabled')" :label="t('common.enable')" min-width="80">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggle(row)" />
          </template>
        </el-table-column>
        <el-table-column prop="actions" :label="t('common.action')" width="140">
          <template #default="{ row }">
            <IconBtn size="small" :icon="VideoPlay" :loading="testing[row.id]" :title="t('common.test')" @click="test(row)" />
            <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="onEdit(row)" />
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="del(row)" />
          </template>
        </el-table-column>
      </TableShell>

      <!-- 编辑弹窗（批50：priority 输入退役——拖拽唯一真源） -->
      <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('common.edit') : t('common.create')" width="560px">
        <el-form :model="form" label-position="top" style="max-width: 480px">
          <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
          <el-form-item label="Provider"><el-input v-model="form.provider" placeholder="deepseek / ark ..." /></el-form-item>
          <el-form-item :label="t('llm.model')"><el-input v-model="form.model" /></el-form-item>
          <el-form-item :label="t('llm.apiKey')"><el-input v-model="form.api_key" type="password" show-password :placeholder="t('common.phEditNoChange')" autocomplete="new-password" /></el-form-item>
          <el-form-item :label="t('llm.baseUrl')"><el-input v-model="form.base_url" /></el-form-item>
          <el-form-item :label="t('cols.contextWindow')"><el-input-number v-model="form.context_window" :min="0" :step="1024" controls-position="right" /></el-form-item>
          <el-form-item :label="t('llm.maxInputTokens')"><el-input-number v-model="form.max_input_tokens" :min="0" controls-position="right" :placeholder="t('llm.phInputTokens')" /></el-form-item>
          <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
          <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        </template>
      </el-dialog>
    </el-card>

    <!-- 用量监控 section（批50 卡片化） -->
    <el-card style="margin-top: 20px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('llm.llmUsageTitle2') }}</span>
          <RefreshBtn @refresh="loadUsage" />
        </div>
      </template>
      <LLMUsageCards :models="usageModels" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Edit, Delete, Plus, VideoPlay, Menu } from '@element-plus/icons-vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import RefreshBtn from '../components/RefreshBtn.vue'
import LLMUsageCards from '../components/LLMUsageCards.vue'
import { fmtTime } from '../utils/fmtTime'
import { apiErr, getLLMModels, createLLMModel, updateLLMModel, deleteLLMModel,
         testLLMModel, reorderLLMModels, getLLMUsageSeries } from '../api'

const { t } = useI18n()
const models = ref([])
const usageModels = ref([])
const dlg = ref(false)
const saving = ref(false)
const testing = ref({})
const modelVisible = ref({})
const modelColDefs = [   // 批53 追加三:ID/Key/ctx 列删(用户裁定——内部键/凭证/配置项不占表)
  { key: 'provider', label: t('llm.providerCol') },
  { key: 'model', label: t('llm.model') }, { key: 'enabled', label: t('common.enable') },
]
const modelColOn = k => modelVisible.value[k] !== false
const emptyForm = () => ({ id: null, name: '', provider: '', model: '', api_key: '', base_url: '',
                           context_window: 32768, max_input_tokens: null, enabled: true })
const form = ref(emptyForm())

const load = async () => { try { models.value = await getLLMModels() } catch (e) { console.error(e) } }
const loadUsage = async () => { try { usageModels.value = (await getLLMUsageSeries()).models || [] } catch (e) { console.error(e) } }

const onAdd = () => { form.value = emptyForm(); dlg.value = true }
const onEdit = (row) => {
  form.value = { ...row, api_key: '' }   // 密钥空起——留空=不改（后端 api_key 空串跳过加密）
  dlg.value = true
}
const onSave = async () => {
  saving.value = true
  try {
    const body = { name: form.value.name, provider: form.value.provider, model: form.value.model,
                   api_key: form.value.api_key, base_url: form.value.base_url,
                   context_window: form.value.context_window,
                   supports_tools: form.value.supports_tools ?? true,   // 批50 盲审 A-P1-1：原值保真（硬编码 true 会静默重置纯文本模型）
                   max_input_tokens: form.value.max_input_tokens, enabled: form.value.enabled }
    if (form.value.id) await updateLLMModel(form.value.id, body)
    else await createLLMModel(body)
    ElMessage.success(t('common.saveSuccess'))
    dlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
const toggle = async (row) => {
  try { await updateLLMModel(row.id, { ...row, api_key: '' }) }
  catch (e) { row.enabled = !row.enabled; ElMessage.error(apiErr(e, t('common.failed'))) }
}
const del = async (row) => {
  try {
    await ElMessageBox.confirm(row.name, t('common.delete'), { type: 'warning' })
    await deleteLLMModel(row.id)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.detail) ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}
const test = async (row) => {
  testing.value[row.id] = true
  try {
    const r = await testLLMModel(row.id)
    ElMessage.success(`${row.name}: ${r.reply?.slice(0, 60) || 'ok'}`)
  } catch (e) { ElMessage.error(apiErr(e, t('common.failed'))) }
  finally { testing.value[row.id] = false }
}

// ——— 拖拽重排（批43/47/50 同款：乐观重排+失败重拉）———
let dragIdx = -1
const onDragStart = (i) => { dragIdx = i }
const onDrop = async (i) => {
  if (dragIdx < 0 || dragIdx === i) return
  const arr = [...models.value]
  const [moved] = arr.splice(dragIdx, 1)
  arr.splice(i, 0, moved)
  models.value = arr   // 乐观
  dragIdx = -1
  try { await reorderLLMModels(arr.map(r => r.id)) }
  catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))); await load() }
}

onMounted(() => { load(); loadUsage() })
</script>

<style scoped>
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
.cooldown-note { color: var(--text-secondary); }
.drag-handle { display: inline-block; width: 12px; height: 10px; cursor: grab;
               background: linear-gradient(180deg, var(--text-secondary) 0 2px, transparent 2px 4px, var(--text-secondary) 4px 6px, transparent 6px 8px, var(--text-secondary) 8px 10px);
               opacity: .55; }
.drag-handle:active { cursor: grabbing; }
</style>
