<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('brokers.manageTitle') }}<el-button type="primary" @click="onAdd">{{ t('common.create') }}</el-button></div></template>
    <el-table :data="brokers">
      <el-table-column prop="provider" label="Provider" width="100" />
      <el-table-column prop="name" :label="t('common.name')" show-overflow-tooltip />
      <el-table-column :label="t('common.credential')" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.enable')" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="260">
        <template #default="{ row }">
          <el-button type="primary" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('brokers.editTitle') : t('brokers.addTitle')" width="560px">
      <el-form :model="form" label-width="120px">
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('brokers.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialJson')">
        <!-- 15号批四: XTP field_schema 静态映射(消灭盲写 JSON;非 XTP 走原 password) -->
        <div v-if="form.provider === 'xtp'" style="display: flex; flex-direction: column; gap: 6px">
          <el-input v-model="credFields['td_host']" :placeholder="t('brokers.phTdHost')" />
          <el-input v-model="credFields['td_port']" :placeholder="t('brokers.phTdPort')" />
          <el-input v-model="credFields['md_host']" :placeholder="t('brokers.phMdHost')" />
          <el-input v-model="credFields['md_port']" :placeholder="t('brokers.phMdPort')" />
          <el-input v-model="credFields['client_id']" :placeholder="t('brokers.phClientId')" />
        </div>
        <el-input v-else v-model="form.credentials" type="password" show-password :placeholder="t('brokers.phCred')" />
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
    <el-table :data="usage.today">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="calls" :label="t('common.todayCalls')" width="100" />
      <el-table-column prop="avg_latency_ms" :label="t('common.avgLatency')" width="120" />
      <el-table-column prop="success_rate" :label="t('common.successRate')"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
    </el-table>
    <div v-if="!usage.today?.length" style="color:#999;font-size:12px;margin-top:var(--sp-2)">{{ t('brokers.noUsage') }}</div>
  </el-card>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getBrokers, createBroker, updateBroker, deleteBroker, testBroker } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const brokers = ref([])
const form = ref(emptyForm())
const saving = ref(false)
const dlg = ref(false)   // 编辑形态弹窗化（DESIGN 新立法）
const testing = ref(0)
const usage = ref({})
const usageLoading = ref(false)
// 15号批四: XTP field_schema 静态映射(credFields 对象 ↔ credentials JSON 串)
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
onMounted(async () => { await load(); await loadUsage() })

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
