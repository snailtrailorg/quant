<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('channels.manageTitle') }}<div style="display:flex; gap:8px; align-items:center"><ColumnSettings storage-key="cols.channels" :columns="colDefs" v-model:visible="visible" /><el-button type="primary" @click="onAdd">{{ t('common.create') }}</el-button></div></div></template>
    <TableShell :data="channels" storage-key="channels">
      <el-table-column v-if="colOn('provider')" prop="provider" label="Provider" min-width="120" />
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
          <el-button type="primary" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </TableShell>
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('channels.editTitle') : t('channels.addTitle')" width="560px">
      <el-form :model="form" label-width="120px">
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('channels.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialWebhook')"><el-input v-model="form.credentials" type="password" show-password :placeholder="t('common.phEditNoChange')" autocomplete="new-password" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import { fmtTime } from '../utils/fmtTime'
import {apiErr,  getChannels, createChannel, updateChannel, deleteChannel, testChannel } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const channels = ref([])

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

function emptyForm() {
  return { provider: 'wechat_work', name: '', credentials: '', enabled: true }
}

const load = async () => { try { channels.value = await getChannels() } catch (e) { console.error(e) } }
onMounted(load)

const onEdit = (row) => { form.value = { ...row, credentials: '' } ; dlg.value = true }
const resetForm = () => { form.value = emptyForm() }
const onAdd = () => { resetForm(); dlg.value = true }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateChannel(form.value.id, form.value)
    else await createChannel(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    dlg.value = false
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
