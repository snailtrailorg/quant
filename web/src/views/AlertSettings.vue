<template>
  <EmptyState v-if="!allowed" :description="t('alerts.noPerm')" />
  <div v-else>
    <el-alert v-if="!cfg.sms_configured" type="info" :closable="false" style="margin-bottom: 12px">
      {{ t('alerts.smsNotConfigured') }}
    </el-alert>
    <!-- 批30：旧按地址订阅（email/sms）不自动迁移——提示重建，防告警漏发（为零不显示） -->
    <el-alert v-if="legacyRows.length" type="warning" :closable="false" style="margin-bottom: 12px">
      {{ t('alerts.legacyBanner', { n: legacyRows.length }) }}
    </el-alert>

    <TableShell :data="rows" storage-key="alert-subs">
      <el-table-column prop="username" :label="t('alerts.subUser')" min-width="150">
        <template #default="{ row }">
          {{ row.nickname ? `${row.username}（${row.nickname}）` : row.username }}
        </template>
      </el-table-column>
      <el-table-column :label="t('alerts.channels')" min-width="220">
        <template #default="{ row }">
          <el-tooltip :content="row.channels.email ? t('alerts.chip.email.okTip') : t('alerts.chip.email.noTip')" placement="top">
            <el-tag :type="row.channels.email ? 'success' : 'info'" size="small" style="margin: 1px">{{ row.channels.email ? t('alerts.chip.email.ok') : t('alerts.chip.email.no') }}</el-tag>
          </el-tooltip>
          <el-tooltip :content="row.channels.sms ? t('alerts.chip.sms.okTip') : t('alerts.chip.sms.noTip')" placement="top">
            <el-tag :type="row.channels.sms ? 'warning' : 'info'" size="small" style="margin: 1px">{{ row.channels.sms ? t('alerts.chip.sms.ok') : t('alerts.chip.sms.no') }}</el-tag>
          </el-tooltip>
          <el-tooltip :content="row.channels.im ? t('alerts.chip.imTip', { n: row.channels.im }) : t('alerts.chip.imZeroTip')" placement="top">
            <el-tag :type="row.channels.im ? 'primary' : 'info'" size="small" style="margin: 1px">{{ row.channels.im ? t('alerts.chip.im', { n: row.channels.im }) : t('alerts.chip.imZero') }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column prop="categories" :label="t('alerts.categories')" min-width="170">
        <template #default="{ row }">
          <el-tag v-for="c in row.categories" :key="c" size="small" style="margin: 1px">{{ t('alerts.cat.' + c) }}</el-tag>
          <span v-if="!row.categories.length" style="color: var(--text-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="min_level" :label="t('alerts.minLevel')" width="120">
        <template #default="{ row }">{{ t('alerts.lvl.' + row.min_level) }}</template>
      </el-table-column>
      <el-table-column prop="enabled" :label="t('common.enable')" width="70">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="toggle(row)" />
        </template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="200">
        <template #default="{ row }">
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="edit(row)" />
          <IconBtn size="small" :icon="VideoPlay" :loading="testing[row.id]" :title="t('alerts.testBtn')" @click="test(row)" />
          <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="del(row)" />
        </template>
      </el-table-column>
    </TableShell>

    <div style="display: flex; gap: 8px; margin-top: 12px">
      <el-button type="primary" @click="add">{{ t('alerts.addSub') }}</el-button>
      <el-button type="warning" @click="smsDlg = true">{{ t('alerts.smsCred') }}</el-button>
    </div>

    <!-- 订阅编辑（批30 用户维度：新增=用户多选循环建行；编辑=用户只读） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="isEdit ? t('alerts.editSub') : t('alerts.addSub')" width="560px">
      <el-form label-width="90px">
        <el-form-item :label="t('alerts.subUser')">
          <el-select v-if="!isEdit" v-model="form.user_ids" multiple filterable style="width: 100%" :placeholder="t('alerts.phSubUser')">
            <el-option v-for="u in cfg.users" :key="u.id" :value="u.id"
                       :label="u.nickname ? `${u.username}（${u.nickname}）` : u.username" />
          </el-select>
          <div v-if="!isEdit" class="sub-limit-hint">{{ t('alerts.subLimit') }}</div>
          <el-input v-else :model-value="form.username" disabled />
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

    <!-- 短信凭证（专用端点,secret 只写不读；批30 加验证码模板第 5 字段） -->
    <el-dialog v-model="smsDlg" :close-on-click-modal="false" :title="t('alerts.smsCred')" width="560px">
      <el-form label-width="140px">
        <el-form-item label="AccessKey ID"><el-input v-model="smsForm.access_key_id" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item label="AccessKey Secret"><el-input v-model="smsForm.access_key_secret" type="password" show-password :placeholder="t('alerts.phKeepBlank')" autocomplete="new-password" /></el-form-item>
        <el-form-item :label="t('alerts.signName')"><el-input v-model="smsForm.sign_name" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.tplCode')"><el-input v-model="smsForm.template_code" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.verifyTplCode')"><el-input v-model="smsForm.verify_template_code" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
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
import TableShell from '../components/TableShell.vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'
import EmptyState from '../components/EmptyState.vue'
import IconBtn from '../components/IconBtn.vue'
import { Edit, VideoPlay, Delete } from '@element-plus/icons-vue'

const { t } = useI18n()
const CATS = ['risk', 'task', 'data', 'system']
const allowed = ref(false)
const isAdmin = localStorage.getItem('role') === 'admin'
const cfg = ref({ subs: [], users: [], sms_configured: false, legacy: [], quota: {} })
const rows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const editingId = ref(null)
const form = reactive({ user_ids: [], username: '', categories: ['risk'], min_level: 'warn', enabled: true })
const testing = reactive({})
const smsDlg = ref(false)
const smsForm = ref({ access_key_id: '', access_key_secret: '', sign_name: '', template_code: '', verify_template_code: '' })

const legacyRows = computed(() => cfg.value.legacy || [])

const load = async () => {
  const d = await api.get('/alerts/config')
  cfg.value = d
  rows.value = d.subs
}

const add = () => {
  isEdit.value = false; editingId.value = null
  Object.assign(form, { user_ids: [], username: '', categories: ['risk'], min_level: 'warn', enabled: true })
  dlg.value = true
}
const edit = (row) => {
  isEdit.value = true; editingId.value = row.id
  Object.assign(form, { user_ids: [row.user_id], username: row.nickname ? `${row.username}（${row.nickname}）` : row.username,
                        categories: [...(row.categories || [])], min_level: row.min_level, enabled: row.enabled })
  dlg.value = true
}

const save = async () => {
  try {
    if (isEdit.value) {
      await api.put(`/alerts/config/${editingId.value}`,
                    { user_id: form.user_ids[0], categories: form.categories, min_level: form.min_level, enabled: form.enabled })
    } else {
      // 用户多选=循环建行（后端行级端点保形）；A-P2-5：已订阅（409 DUPLICATE_SUB）幂等跳过，
      // 其余失败收集后统一报——部分成功行落表可见
      if (!form.user_ids.length) { ElMessage.warning(t('alerts.phSubUser')); return }
      const failed = []
      for (const uid of form.user_ids) {
        try {
          await api.post('/alerts/config', { user_id: uid, categories: form.categories, min_level: form.min_level, enabled: form.enabled })
        } catch (e) {
          if (e?.code === 'DUPLICATE_SUB') continue
          failed.push(uid)
        }
      }
      if (failed.length) throw new Error(`${failed.length}`)
    }
    dlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(e?.message ? t('alerts.partialFail', { n: e.message }) : (e?.detail || t('common.failed'))) }
}

const toggle = async (row) => {
  try {
    await api.put(`/alerts/config/${row.id}`, { categories: row.categories,
                                                min_level: row.min_level, enabled: row.enabled })
    ElMessage.success(t('common.success'))
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error(e?.detail || t('common.failed'))
  }
}

const del = async (row) => {
  try {
    await ElMessageBox.confirm(row.nickname ? `${row.username}（${row.nickname}）` : row.username,
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
    r.ok ? ElMessage.success(r.detail) : ElMessage.warning(r.detail)
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
<style scoped>
.sub-limit-hint { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: 4px; line-height: 1.4; }
</style>
