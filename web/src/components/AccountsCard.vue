<template>
  <!-- 批55b:API 密钥账户卡(实盘任务绑定专用——批55b 盲审 A-P1 修:TradingAccounts 重写误删写入口断链)。
      旧页签全功能 CRUD 精简保留;strategy_account.account_id(Text)+LiveTask 下拉消费 /api/account 不变。 -->
  <el-card style="margin-top: 12px">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-weight: 600">{{ t('interfaces.accountsTitle') }}</span>
        <IconBtn :icon="Plus" :title="t('common.create')" @click="openAdd" />
      </div>
    </template>
    <div class="order-note">{{ t('interfaces.accountsNote') }}</div>
    <TableShell :data="accounts" :loading="loading" storage-key="api-key-accounts">
      <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip />
      <el-table-column prop="exchange" :label="t('interfaces.providerLabel')" min-width="140" />
      <el-table-column prop="api_key_hint" :label="t('interfaces.accountsKeyHint')" min-width="180">
        <template #default="{ row }">
          <code style="font-family: var(--font-num); font-size: var(--fs-foot)">{{ row.api_key_hint || '—' }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="enabled" :label="t('common.enable')" min-width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="110">
        <template #default="{ row }">
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')" @click="del(row)" />
        </template>
      </el-table-column>
    </TableShell>

    <el-dialog v-model="showForm" :close-on-click-modal="false"
               :title="form._edit ? t('interfaces.accountsEdit') : t('interfaces.accountsAdd')" width="520px">
      <el-form :model="form" label-width="130px">
        <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
        <el-form-item :label="t('interfaces.providerLabel')">
          <el-select v-model="form.exchange" style="width: 100%">
            <el-option v-for="ex in ['xtp', 'binance_perp', 'okx_perp']" :key="ex" :value="ex" :label="ex" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('interfaces.accountsKeyHint')"><el-input v-model="form.api_key_hint" /></el-form-item>
        <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
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
import TableShell from './TableShell.vue'
import IconBtn from './IconBtn.vue'
import { Plus, Edit, Delete } from '@element-plus/icons-vue'
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
const openAdd = () => { form.value = { name: '', exchange: 'xtp', api_key_hint: '', enabled: true, _edit: false }; showForm.value = true }
const openEdit = (row) => { form.value = { ...row, _edit: true }; showForm.value = true }
const save = async () => {
  saving.value = true
  try {
    if (form.value._edit) await api.post(`/account/${form.value.id}`, form.value)
    else await api.post('/account', form.value)
    ElMessage.success(t('common.saveSuccess'))
    showForm.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
const del = async (row) => {
  try { await ElMessageBox.confirm(t('interfaces.accountsConfirmDelete', { name: row.name }), t('common.tip'), { type: 'warning' }) }
  catch { return }
  try {
    await api.delete(`/account/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.response) ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}
onMounted(load)
</script>

<style scoped>
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
</style>
