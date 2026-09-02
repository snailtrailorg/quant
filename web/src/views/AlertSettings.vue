<template>
  <div v-if="!allowed" class="empty-cell" style="padding: 24px 0">{{ t('alerts.noPerm') }}</div>
  <div v-else>
    <el-alert v-if="!cfg.sms_configured" type="info" :closable="false" style="margin-bottom: 12px">
      {{ t('alerts.smsNotConfigured') }}
    </el-alert>

    <el-table :data="rows">
      <el-table-column :label="t('common.type')" width="90">
        <template #default="{ row }">
          <el-tag :type="{ im: 'primary', email: 'success', sms: 'warning' }[row.channel]">
            {{ t('alerts.channel.' + row.channel) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="target" :label="t('alerts.target')" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.channel === 'im' ? botName(row.target) : (row.target || '—') }}
        </template>
      </el-table-column>
      <el-table-column :label="t('alerts.categories')" min-width="170">
        <template #default="{ row }">
          <el-tag v-for="c in row.categories" :key="c" size="small" style="margin: 1px">{{ t('alerts.cat.' + c) }}</el-tag>
          <span v-if="!row.categories.length" style="color: var(--text-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('alerts.minLevel')" width="120">
        <template #default="{ row }">{{ t('alerts.lvl.' + row.min_level) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.enable')" width="70">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="200">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="edit(row)">{{ t('common.edit') }}</el-button>
          <el-button size="small" type="warning" :loading="testing[row.id]" @click="test(row)">{{ t('alerts.testBtn') }}</el-button>
          <el-button size="small" type="danger" @click="del(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div style="display: flex; gap: 8px; margin-top: 12px">
      <el-button type="primary" @click="add">{{ t('alerts.addSub') }}</el-button>
      <el-button type="warning" @click="smsDlg = true">{{ t('alerts.smsCred') }}</el-button>
    </div>

    <!-- 订阅编辑（新增/修改共用；类型驱动目标控件） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="isEdit ? t('alerts.editSub') : t('alerts.addSub')" width="560px">
      <el-form label-width="90px">
        <el-form-item :label="t('common.type')">
          <el-select v-model="form.channel" style="width: 100%" :disabled="isEdit">
            <el-option value="im" :label="t('alerts.channel.im')" />
            <el-option value="email" :label="t('alerts.channel.email')" />
            <el-option value="sms" :label="t('alerts.channel.sms')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('alerts.target')">
          <el-select v-if="form.channel === 'im'" v-model="form.target" style="width: 100%">
            <el-option v-for="b in cfg.im_bots" :key="b.id" :value="String(b.id)"
                       :label="`${b.name}（${t('alerts.channel.' + b.provider)} · ${t('alerts.bound')}: ${b.bound_users}）`"
                       :disabled="!b.enabled" />
          </el-select>
          <el-input v-else-if="form.channel === 'email'" v-model="form.target" :placeholder="t('alerts.phEmail')" />
          <el-input v-else v-model="form.target" :placeholder="t('alerts.phPhone')" />
        </el-form-item>
        <el-form-item v-if="form.channel === 'im' && selBot && selBot.bound_users === 0" :label="''">
          <span style="color: var(--warn); font-size: var(--fs-foot)">{{ t('alerts.noBinding') }}</span>
        </el-form-item>
        <el-form-item :label="t('alerts.categories')">
          <el-checkbox-group v-model="form.categories">
            <el-checkbox v-for="c in CATS" :key="c" :value="c">{{ t('alerts.cat.' + c) }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item :label="t('alerts.minLevel')">
          <el-select v-model="form.min_level" style="width: 100%">
            <el-option value="warn" :label="t('alerts.lvl.warn')" />
            <el-option value="critical" :label="t('alerts.lvl.critical')" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.enable')">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 短信凭证（专用端点,secret 只写不读） -->
    <el-dialog v-model="smsDlg" :close-on-click-modal="false" :title="t('alerts.smsCred')" width="560px">
      <el-form label-width="140px">
        <el-form-item label="AccessKey ID"><el-input v-model="smsForm.access_key_id" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item label="AccessKey Secret"><el-input v-model="smsForm.access_key_secret" type="password" show-password :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.signName')"><el-input v-model="smsForm.sign_name" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.tplCode')"><el-input v-model="smsForm.template_code" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="smsDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveSms">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const CATS = ['risk', 'task', 'data', 'system']
const allowed = ref(false)
const isAdmin = localStorage.getItem('role') === 'admin'
const cfg = ref({ channels: [], sms_configured: false, im_bots: [], quota: {} })
const rows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const editingId = ref(null)
const form = reactive({ channel: 'im', target: '', categories: ['risk'], min_level: 'warn', enabled: true })
const testing = reactive({})
const smsDlg = ref(false)
const smsForm = ref({ access_key_id: '', access_key_secret: '', sign_name: '', template_code: '' })

const selBot = computed(() => {
  const b = cfg.value.im_bots.find(x => String(x.id) === form.target)
  return b && b.enabled ? b : null
})
const botName = tid => cfg.value.im_bots.find(x => String(x.id) === String(tid))?.name || tid

const load = async () => {
  const d = await api.get('/alerts/config')
  cfg.value = d
  rows.value = d.channels
}

const add = () => {
  isEdit.value = false; editingId.value = null
  Object.assign(form, { channel: 'im', target: '', categories: ['risk'], min_level: 'warn', enabled: true })
  dlg.value = true
}
const edit = (row) => {
  isEdit.value = true; editingId.value = row.id
  Object.assign(form, { channel: row.channel, target: row.target || '',
                        categories: [...(row.categories || [])], min_level: row.min_level, enabled: row.enabled })
  dlg.value = true
}

const save = async () => {
  try {
    if (isEdit.value) {
      await api.put(`/alerts/config/${editingId.value}`, { ...form })
    } else {
      const r = await api.post('/alerts/config', { ...form })
      editingId.value = r.id
    }
    dlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(e?.detail || t('common.failed')) }
}

const toggle = async (row) => {
  try {
    await api.put(`/alerts/config/${row.id}`, { target: row.target, categories: row.categories,
                                                min_level: row.min_level, enabled: row.enabled })
    ElMessage.success(t('common.success'))
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error(e?.detail || t('common.failed'))
  }
}

const del = async (row) => {
  try {
    await ElMessageBox.confirm(`${t('alerts.channel.' + row.channel)} · ${row.channel === 'im' ? botName(row.target) : row.target}`,
                               t('common.delete'), { type: 'warning' })
    await api.delete(`/alerts/config/${row.id}`)
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { if (e?.detail) ElMessage.error(e.detail) }
}

const saveSms = async () => {
  try {
    await api.put('/alerts/sms-config', smsForm.value)
    smsDlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(e?.detail || t('common.failed')) }
}

const test = async (row) => {
  testing[row.id] = true
  try {
    const r = await api.post('/alerts/test', { id: row.id })
    r.ok ? ElMessage.success(`${t('alerts.channel.' + row.channel)}: ${r.detail}`) : ElMessage.warning(`${t('alerts.channel.' + row.channel)}: ${r.detail}`)
  } catch (e) {
    ElMessage.error(e?.detail || t('common.failed'))
  } finally { testing[row.id] = false }
}

onMounted(async () => {
  try {
    const me = await api.get('/auth/me')
    allowed.value = (me.permissions || []).includes('alerts_config')
  } catch { allowed.value = isAdmin }
  if (allowed.value) await load().catch(() => {})
})
</script>
