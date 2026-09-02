<template>
  <!-- 09-A4 交易账户页:账户 CRUD + API 密钥表(名称/交易所/hint 回显)——后端 /api/account 端点齐备,批 1 补 UI -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('tradingAccounts.title') }}</span>
        <el-button type="primary" size="small" @click="showForm = true">{{ t('common.create') }}</el-button>
      </div>
    </template>
    <el-table :data="accounts" v-loading="loading">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" :label="t('common.name')" show-overflow-tooltip />
      <el-table-column prop="exchange" :label="t('tradingAccounts.exchange')" width="120" />
      <el-table-column :label="t('tradingAccounts.apiKeyHint')" width="180">
        <template #default="{ row }">
          <code style="font-family: var(--font-num); font-size: 12px">{{ row.api_key_hint || '—' }}</code>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.status')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" :label="t('common.createdAt')" width="160">
        <template #default="{ row }">{{ (row.created_at || '').slice(0, 19) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="edit(row)">{{ t('common.edit') }}</el-button>
          <el-button size="small" type="danger" @click="del(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="!accounts.length && !loading" style="color: var(--text-secondary); padding: 20px; text-align: center">{{ t('tradingAccounts.empty') }}</div>

    <!-- 创建/编辑弹窗 -->
    <el-dialog v-model="showForm" :close-on-click-modal="false" :title="form._edit ? t('common.edit') : t('common.create')" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
        <el-form-item :label="t('tradingAccounts.exchange')">
          <el-select v-model="form.exchange" style="width: 100%">
            <el-option v-for="ex in ['xtp', 'binance', 'okx']" :key="ex" :value="ex" :label="ex.toUpperCase()" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('tradingAccounts.apiKey')"><el-input v-model="form.api_key_hint" /></el-form-item>
        <el-form-item :label="t('common.status')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'

const { t } = useI18n()
const accounts = ref([])
const loading = ref(false)
const showForm = ref(false)
const saving = ref(false)
const form = ref({ name: '', exchange: 'xtp', api_key_hint: '', enabled: true, _edit: false })

const load = async () => {
  loading.value = true
  try { accounts.value = await api.get('/account') || [] }
  catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
  finally { loading.value = false }
}
const edit = (row) => {
  form.value = { ...row, _edit: true }
  showForm.value = true
}
const save = async () => {
  saving.value = true
  try {
    if (form.value._edit) {
      await api.post(`/account/${form.value.id}`, form.value)
    } else {
      await api.post('/account', form.value)
    }
    ElMessage.success(t('common.saveSuccess'))
    showForm.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
const del = async (row) => {
  try {
    await ElMessageBox.confirm(t('tradingAccounts.confirmDelete', { name: row.name }), t('common.confirm'), { type: 'warning' })
    await api.delete(`/account/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.response) ElMessage.error(t('common.failed')) }
}
onMounted(load)
</script>
