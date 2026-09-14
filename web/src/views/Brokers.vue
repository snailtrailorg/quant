<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('brokers.manageTitle') }}<div style="display:flex; gap:8px; align-items:center"><ColumnSettings storage-key="cols.brokers" :columns="colDefs" v-model:visible="visible" /><IconBtn :icon="Plus" :title="t('common.create')" @click="onAdd" /></div></div></template>
    <TableShell :data="brokers" storage-key="brokers">
      <el-table-column v-if="colOn('provider')" prop="provider" label="Provider" min-width="100" />
      <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip />
      <el-table-column prop="has_credentials" :label="t('common.credential')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="enabled" :label="t('common.enable')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column v-if="colOn('updated_at')" prop="updated_at" :label="t('common.updatedAt')" min-width="160">
        <template #default="{ row }">{{ row.updated_at ? fmtTime.full(row.updated_at) : '-' }}</template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="250">
        <template #default="{ row }">
          <IconBtn size="small" :icon="VideoPlay" :title="t('common.test')" @click="onTest(row.id)" :loading="testing === row.id" />
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="onEdit(row)" />
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')" @click="onDelete(row.id)" />
        </template>
      </el-table-column>
    </TableShell>
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('brokers.editTitle') : t('brokers.addTitle')" width="560px">
      <el-form :model="form" label-width="120px">
      <el-form-item label="Provider">
        <el-select v-model="form.provider" style="width: 100%">
          <el-option label="XTP" value="xtp" />
          <el-option :label="t('common.binance')" value="binance_perp" />
          <el-option label="OKX" value="okx_perp" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialJson')">
        <!-- wd-15 批四: XTP field_schema 静态映射(消灭盲写 JSON;非 XTP 走原 password) -->
        <div v-if="form.provider === 'xtp'" style="display: flex; flex-direction: column; gap: 6px">
          <el-input v-model="credFields['td_host']" :placeholder="t('brokers.phTdHost')" />
          <el-input v-model="credFields['td_port']" :placeholder="t('brokers.phTdPort')" />
          <el-input v-model="credFields['md_host']" :placeholder="t('brokers.phMdHost')" />
          <el-input v-model="credFields['md_port']" :placeholder="t('brokers.phMdPort')" />
          <el-input v-model="credFields['client_id']" :placeholder="t('brokers.phClientId')" />
        </div>
        <el-input v-else v-model="form.credentials" type="password" show-password :placeholder="t('brokers.phCred')" autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
      </template>
    </el-dialog>
  </el-card>

  <!-- P2-4 通道用量监控 -->
  <el-card style="margin-top: 20px" v-loading="usageLoading">
    <template #header>{{ t('brokers.usageTitle') }}</template>
    <TableShell :data="usage.today" storage-key="broker-usage">
      <el-table-column prop="provider" label="Provider" min-width="120" />
      <el-table-column prop="calls" :label="t('common.todayCalls')" min-width="100" />
      <el-table-column prop="avg_latency_ms" :label="t('common.avgLatency')" min-width="120" />
      <el-table-column prop="success_rate" :label="t('common.successRate')"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
    </TableShell>
    <div v-if="!usage.today?.length" style="color:var(--text-secondary);font-size:12px;margin-top:var(--sp-2)">{{ t('brokers.noUsage') }}</div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import { Plus, VideoPlay, Edit, Delete } from '@element-plus/icons-vue'
import { fmtTime } from '../utils/fmtTime'
import {apiErr,  getBrokers, createBroker, updateBroker, deleteBroker, testBroker } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const brokers = ref([])

// 批17 列显示配置：更新时间默认隐；名称/凭证/启用/操作恒显不进 defs
const colDefs = computed(() => [
  { key: 'provider', label: 'Provider' },
  { key: 'updated_at', label: t('common.updatedAt'), hidden: true },
])
const visible = ref([])
const colOn = k => visible.value.includes(k)

const form = ref(emptyForm())
const saving = ref(false)
const dlg = ref(false)   // 编辑形态弹窗化（DESIGN 新立法）
const testing = ref(0)
const usage = ref({})
const usageLoading = ref(false)
// wd-15 批四: XTP field_schema 静态映射(credFields 对象 ↔ credentials JSON 串)
const credFields = ref({})
watch(() => form.value.provider, (pv) => {
  if (pv === 'xtp' && !Object.keys(credFields.value).length) {
    try { credFields.value = JSON.parse(form.value.credentials || '{}') } catch { credFields.value = {} }
  }
}, { immediate: true })

const loadUsage = async () => { usageLoading.value = true; try { usage.value = await api.get('/broker-usage') } catch {} finally { usageLoading.value = false } }

function emptyForm() {
  return { provider: 'xtp', name: '', credentials: '', enabled: true }
}

const load = async () => { try { brokers.value = await getBrokers() } catch (e) { console.error(e) } }
onMounted(async () => { await Promise.all([load(), loadUsage()]) })   // 批9：互不依赖→并发（对齐 LLMModels 同构页）

const onEdit = (row) => { form.value = { ...row, credentials: '' }; credFields.value = {} ; dlg.value = true }   // 补审F-P0:清字段防 A 凭据串进 C
const resetForm = () => { form.value = emptyForm(); credFields.value = {} }   // 补审F-P0
const onAdd = () => { resetForm(); dlg.value = true }

const onSave = async () => {
  serializeCred()
  saving.value = true
  try {
    if (form.value.id) await updateBroker(form.value.id, form.value)
    else await createBroker(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteBroker(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testBroker(id)
    if (r.ok) ElMessage.success(t('common.credComplete'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}

// XTP field_schema 序列化:保存时 credFields→JSON 串
const serializeCred = () => {
  if (form.value.provider === 'xtp' && Object.keys(credFields.value).length) {
    form.value.credentials = JSON.stringify(credFields.value)
  }
}
</script>
