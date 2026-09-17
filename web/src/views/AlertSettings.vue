<template>
  <div>
    <!-- 批30：旧按地址订阅（email/sms）不自动迁移——提示重建，防告警漏发（为零不显示） -->
    <el-alert v-if="legacyRows.length" type="warning" :closable="false" style="margin-bottom: 12px"
              :title="t('alerts.legacyBanner', { n: legacyRows.length })" />
    <el-alert v-if="!cfg.sms_configured" type="info" :closable="false" style="margin-bottom: 12px"
              :title="t('alerts.smsNotConfigured')" />

    <!-- 批34：一行一用户+通道勾选（用户口述重设计）；创建/凭证收编 header 图标钮 -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">{{ t('alerts.page.title') }}</span>
          <span style="display: flex; gap: 8px">
            <IconBtn :icon="Plus" :title="t('alerts.addSub')" @click="add" />
            <IconBtn :icon="Message" :title="t('alerts.smsCred')" @click="smsDlg = true" />
          </span>
        </div>
      </template>
      <EmptyState v-if="!allowed" :description="t('alerts.noPerm')" />
      <TableShell :data="rows" storage-key="alert-subs">
        <el-table-column prop="username" :label="t('alerts.subUser')" min-width="150">
          <template #default="{ row }">{{ row.nickname ? `${row.username}（${row.nickname}）` : row.username }}</template>
        </el-table-column>
        <el-table-column :label="t('alerts.channels')" min-width="260">
          <template #default="{ row }">
            <!-- 批34 三态：null=全通道自动（按 avail 联动亮灰）/[]=零勾选/非空=已选亮+未勾可用灰 -->
            <template v-if="row.channels_sel === null">
              <el-tooltip :content="row.channels_avail.email ? t('alerts.allChannelsTip') : t('alerts.chip.email.noTip')" placement="top">
                <el-tag :type="row.channels_avail.email ? 'success' : 'info'" size="small" style="margin: 1px">{{ t('alerts.chip.email.' + (row.channels_avail.email ? 'ok' : 'no')) }}</el-tag>
              </el-tooltip>
              <el-tooltip :content="row.channels_avail.sms ? t('alerts.allChannelsTip') : t('alerts.chip.sms.noTip')" placement="top">
                <el-tag :type="row.channels_avail.sms ? 'success' : 'info'" size="small" style="margin: 1px">{{ t('alerts.chip.sms.' + (row.channels_avail.sms ? 'ok' : 'no')) }}</el-tag>
              </el-tooltip>
              <el-tooltip v-if="row.channels_avail.bots.length" :content="t('alerts.allChannelsTip')" placement="top">
                <el-tag v-for="b in row.channels_avail.bots" :key="b.id" type="success" size="small" style="margin: 1px">{{ b.name }}</el-tag>
              </el-tooltip>
              <el-tooltip v-else :content="t('alerts.chip.imZeroTip')" placement="top">
                <el-tag type="info" size="small" style="margin: 1px">{{ t('alerts.chip.imZero') }}</el-tag>
              </el-tooltip>
            </template>
            <el-tooltip v-else-if="!row.channels_sel.length" :content="t('alerts.noChannelSelTip')" placement="top">
              <el-tag type="info" size="small">{{ t('alerts.noChannelSel') }}</el-tag>
            </el-tooltip>
            <template v-else>
              <el-tooltip :content="selHas(row, 'email') ? t('alerts.chip.email.okTip') : t('alerts.chipSkipTip')" placement="top">
                <el-tag v-if="row.channels_avail.email" :type="selHas(row, 'email') ? 'success' : 'info'" size="small" style="margin: 1px">{{ t('alerts.chip.email.ok') }}</el-tag>
              </el-tooltip>
              <el-tooltip :content="selHas(row, 'sms') ? t('alerts.chip.sms.okTip') : t('alerts.chipSkipTip')" placement="top">
                <el-tag v-if="row.channels_avail.sms" :type="selHas(row, 'sms') ? 'success' : 'info'" size="small" style="margin: 1px">{{ t('alerts.chip.sms.ok') }}</el-tag>
              </el-tooltip>
              <template v-for="b in row.channels_avail.bots" :key="b.id">
                <el-tag v-if="selHas(row, 'im:' + b.id)" type="primary" size="small" style="margin: 1px">{{ b.name }}</el-tag>
                <el-tooltip v-else :content="t('alerts.chipSkipTip')" placement="top">
                  <el-tag type="info" size="small" style="margin: 1px">{{ b.name }}</el-tag>
                </el-tooltip>
              </template>
            </template>
          </template>
        </el-table-column>
        <el-table-column prop="categories" :label="t('alerts.categories')" min-width="170">
          <template #default="{ row }">
            <el-tag v-for="c in row.categories" :key="c" size="small" style="margin: 1px">{{ t('alerts.cat.' + c) }}</el-tag>
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
    </el-card>

    <!-- 订阅编辑（批34：单用户+通道勾选三态——全勾提交 null=自动；一个不勾=该用户暂不接收） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="isEdit ? t('alerts.editSub') : t('alerts.addSub')" width="560px">
      <el-form label-width="90px">
        <el-form-item :label="t('alerts.subUser')">
          <el-select v-if="!isEdit" v-model="form.user_id" filterable style="width: 100%" :placeholder="t('alerts.phSubUser')"
                     @change="onUserPicked">
            <el-option v-for="u in cfg.users" :key="u.id" :value="u.id"
                       :label="u.nickname ? `${u.username}（${u.nickname}）` : u.username" />
          </el-select>
          <div v-if="!isEdit" class="sub-limit-hint">{{ t('alerts.subLimit') }}</div>
          <el-input v-else :model-value="form.username" disabled />
        </el-form-item>
        <el-form-item :label="t('alerts.channels')">
          <template v-if="form.user_id">
            <el-checkbox-group v-model="form.channels">
              <el-checkbox value="email" :disabled="!formAvail.email">{{ t('alerts.chan.email') }}</el-checkbox>
              <el-checkbox value="sms" :disabled="!formAvail.sms">{{ t('alerts.chan.sms') }}</el-checkbox>
              <el-checkbox v-for="b in formAvail.bots" :key="'im:' + b.id" :value="'im:' + b.id">{{ b.name }}</el-checkbox>
            </el-checkbox-group>
            <div class="sub-limit-hint">{{ t('alerts.chanHint') }}</div>
          </template>
          <span v-else style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('alerts.pickUserFirst') }}</span>
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
import api, { apiErr } from '../api'
import IconBtn from '../components/IconBtn.vue'
import EmptyState from '../components/EmptyState.vue'
import { Edit, VideoPlay, Delete, Plus, Message } from '@element-plus/icons-vue'

const { t } = useI18n()
const CATS = ['risk', 'task', 'data', 'system']
const allowed = ref(false)
const isAdmin = localStorage.getItem('role') === 'admin'
const cfg = ref({ subs: [], users: [], sms_configured: false, legacy: [], quota: {} })
const rows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const editingId = ref(null)
const form = reactive({ user_id: null, username: '', channels: [], categories: ['risk'], min_level: 'warn', enabled: true })
const testing = reactive({})
const smsDlg = ref(false)
const smsForm = ref({ access_key_id: '', access_key_secret: '', sign_name: '', template_code: '', verify_template_code: '' })

const legacyRows = computed(() => cfg.value.legacy || [])
const EMPTY_AVAIL = { email: false, sms: false, bots: [] }

const load = async () => {
  const d = await api.get('/alerts/config')
  cfg.value = d
  rows.value = d.subs
}

const availOf = (uid) => (cfg.value.users || []).find(u => u.id === uid)?.channels || EMPTY_AVAIL
const formAvail = computed(() => availOf(form.user_id))
// 全勾键集（avail 内全部可选通道）——保存时与勾选集比对，全勾归一提交 null（全通道自动）
const allKeysOf = (avail) => [
  ...(avail.email ? ['email'] : []),
  ...(avail.sms ? ['sms'] : []),
  ...avail.bots.map(b => `im:${b.id}`),
]
const selHas = (row, key) => (row.channels_sel || []).includes(key)

const onUserPicked = () => {   // 新增选用户后默认全勾（=全通道自动）
  form.channels = allKeysOf(formAvail.value)
}

const add = () => {
  isEdit.value = false; editingId.value = null
  Object.assign(form, { user_id: null, username: '', channels: [], categories: ['risk'], min_level: 'warn', enabled: true })
  dlg.value = true
}
const edit = (row) => {
  isEdit.value = true; editingId.value = row.id
  const avail = row.channels_avail || EMPTY_AVAIL
  Object.assign(form, {
    user_id: row.user_id, username: row.nickname ? `${row.username}（${row.nickname}）` : row.username,
    channels: row.channels_sel === null ? allKeysOf(avail) : [...(row.channels_sel || [])],   // null=全勾回显；已剥离失效键
    categories: [...(row.categories || [])], min_level: row.min_level, enabled: row.enabled,
  })
  dlg.value = true
}

const save = async () => {
  try {
    const avail = formAvail.value
    const sel = allKeysOf(avail).every(k => form.channels.includes(k))
      ? null : [...form.channels]   // 全勾（含零 avail 空集空真）→null（全通道自动——补了联系方式自动纳入）；部分/零勾→数组（[]=暂不接收，前提=存在可勾框，盲审 A-P1-1）
    if (isEdit.value) {
      await api.put(`/alerts/config/${editingId.value}`,
                    { user_id: form.user_id, channels: sel, categories: form.categories, min_level: form.min_level, enabled: form.enabled })
    } else {
      if (!form.user_id) { ElMessage.warning(t('alerts.phSubUser')); return }
      await api.post('/alerts/config', { user_id: form.user_id, channels: sel, categories: form.categories, min_level: form.min_level, enabled: form.enabled })
    }
    dlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) {
    if (e?.code === 'DUPLICATE_SUB') { ElMessage.warning(t('alerts.userSubscribed')); return }
    ElMessage.error(apiErr(e, t('common.failed')))
  }
}

const toggle = async (row) => {   // 不带 channels 键——后端沿用现值不重置勾选（批34 B-P1-2）
  try {
    await api.put(`/alerts/config/${row.id}`, { categories: row.categories,
                                                min_level: row.min_level, enabled: row.enabled })
    ElMessage.success(t('common.success'))
  } catch (e) {
    row.enabled = !row.enabled
    ElMessage.error(apiErr(e, t('common.failed')))
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
  } catch (e) { ElMessage.error(apiErr(e, t('common.failed'))) }
}

const test = async (row) => {
  testing[row.id] = true
  try {
    const r = await api.post('/alerts/test', { id: row.id })
    r.ok ? ElMessage.success(r.detail) : ElMessage.warning(r.detail)
  } catch (e) {
    ElMessage.error(apiErr(e, t('common.failed')))
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
