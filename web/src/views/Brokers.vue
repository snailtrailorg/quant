<template>
  <el-card>
    <template #header>{{ t('brokers.manageTitle') }}</template>
    <el-table :data="brokers" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="100" />
      <el-table-column prop="name" :label="t('common.name')" />
      <el-table-column :label="t('common.credential')" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'" size="small">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
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
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? t('brokers.editTitle') : t('brokers.addTitle') }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('brokers.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialJson')">
        <el-input v-model="form.credentials" type="password" show-password :placeholder="t('brokers.phCred')" style="width:340px" />
      </el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button size="small" type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        <el-button size="small" @click="resetForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </el-card>

  <!-- P2-4 通道用量监控 -->
  <el-card style="margin-top: 20px" v-loading="usageLoading">
    <template #header>{{ t('brokers.usageTitle') }}</template>
    <el-table :data="usage.today" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="calls" :label="t('common.todayCalls')" width="100" />
      <el-table-column prop="avg_latency_ms" :label="t('common.avgLatency')" width="120" />
      <el-table-column prop="success_rate" :label="t('common.successRate')"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
    </el-table>
    <div v-if="!usage.today?.length" style="color:#999;font-size:12px;margin-top:8px">{{ t('brokers.noUsage') }}</div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getBrokers, createBroker, updateBroker, deleteBroker, testBroker } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const brokers = ref([])
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)
const usage = ref({})
const usageLoading = ref(false)

const loadUsage = async () => { usageLoading.value = true; try { usage.value = await api.get('/broker-usage') } catch {} finally { usageLoading.value = false } }

function emptyForm() {
  return { provider: 'xtp', name: '', credentials: '', enabled: true }
}

const load = async () => { try { brokers.value = await getBrokers() } catch (e) { console.error(e) } }
onMounted(async () => { await load(); await loadUsage() })

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateBroker(form.value.id, form.value)
    else await createBroker(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || t('common.saveFailed')) }
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
</script>
