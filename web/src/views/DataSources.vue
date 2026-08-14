<template>
  <el-card>
    <template #header>{{ t('dataSources.title') }}</template>
    <el-card v-if="usage.today && usage.today.length" shadow="never" style="margin-bottom: 12px">
      <div style="font-weight: bold; margin-bottom: 8px">{{ t('dataSources.usageTitle') }}</div>
      <el-table :data="usage.today" size="small">
        <el-table-column prop="provider" label="Provider" width="120" />
        <el-table-column prop="calls" :label="t('common.calls')" width="100" />
        <el-table-column prop="records" :label="t('common.records')" width="100" />
        <el-table-column :label="t('common.failures')" width="80">
          <template #default="{ row }"><el-tag :type="row.failures > 0 ? 'danger' : 'success'" size="small">{{ row.failures }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="avg_latency" :label="t('common.avgLatency')" width="110" />
      </el-table>
    </el-card>
    <el-table :data="sources" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="name" :label="t('common.name')" />
      <el-table-column :label="t('common.credential')" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'" size="small">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="usage_limit" :label="t('common.dailyLimit')" width="80" />
      <el-table-column :label="t('common.enable')" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button size="small" type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button size="small" type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? t('dataSources.editTitle') : t('dataSources.addTitle') }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('dataSources.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialToken')"><el-input v-model="form.credentials" type="password" show-password :placeholder="t('common.phEditNoChange')" /></el-form-item>
      <el-form-item :label="t('common.dailyLimit')"><el-input-number v-model="form.usage_limit" :min="0" controls-position="right" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        <el-button @click="resetForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getDataSources, createDataSource, updateDataSource, deleteDataSource, testDataSource, getDataSourceUsage } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const sources = ref([])
const usage = ref({ today: [], trend: [] })
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { provider: 'tushare', name: '', credentials: '', usage_limit: null, enabled: true }
}

const load = async () => { try { sources.value = await getDataSources() } catch (e) { console.error(e) } }
const loadUsage = async () => { try { usage.value = await getDataSourceUsage() } catch (e) { console.error(e) } }
onMounted(() => { load(); loadUsage() })

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateDataSource(form.value.id, form.value)
    else await createDataSource(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || t('common.saveFailed')) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteDataSource(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testDataSource(id)
    if (r.ok) ElMessage.success(t('common.connectSuccess'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}
</script>
