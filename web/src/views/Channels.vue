<template>
  <el-card>
    <template #header>{{ t('channels.manageTitle') }}</template>
    <el-table :data="channels">
      <el-table-column prop="provider" label="Provider" width="120" />
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
    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? t('channels.editTitle') : t('channels.addTitle') }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('channels.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialWebhook')"><el-input v-model="form.credentials" type="password" show-password :placeholder="t('common.phEditNoChange')" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        <el-button type="primary" @click="resetForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getChannels, createChannel, updateChannel, deleteChannel, testChannel } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const channels = ref([])
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { provider: 'wechat_work', name: '', credentials: '', enabled: true }
}

const load = async () => { try { channels.value = await getChannels() } catch (e) { console.error(e) } }
onMounted(load)

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateChannel(form.value.id, form.value)
    else await createChannel(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteChannel(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testChannel(id)
    if (r.ok) ElMessage.success(t('common.sendSuccess'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}
</script>
