<template>
  <div>
    <!-- 批42：两条过渡横幅已删（用户裁定）——理由见下。 -->

    <!-- 批34：一行一用户+通道勾选（用户口述重设计）；创建/凭证收编 header 图标钮 -->
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">{{ t('alerts.page.title') }}</span>
          <span style="display: flex; gap: 8px">
            <IconBtn :icon="Plus" :title="t('alerts.addSub')" @click="add" />   <!-- 批37：短信凭证入口迁集成中心 -->
          </span>
        </div>
      </template>
      <EmptyState v-if="!allowed" :description="t('alerts.noPerm')" />
      <TableShell :data="rows" storage-key="alert-subs">
        <el-table-column prop="username" :label="t('alerts.subUser')" min-width="150">
          <template #default="{ row }">{{ row.nickname ? `${row.username}（${row.nickname}）` : row.username }}</template>
        </el-table-column>
        <el-table-column :label="t('alerts.channels')" min-width="300">
          <template #default="{ row }">
            <!-- 批48 四态统一渲染（全通道恒显——治 tooltip 吞 chip+两态不一致+停用无处显示）：
                 on=会发(亮)/off=可勾未勾(灰)/disabled=未配置或停用未勾(灰)/on_disabled=已勾但停用(灰+✓) -->
            <el-tooltip v-for="c in chipsOf(row)" :key="c.key" :content="c.tip" placement="top">
              <el-tag :type="c.state === 'on' ? 'success' : 'info'" size="small" style="margin: 1px">{{ c.label }}</el-tag>
            </el-tooltip>
            <el-tooltip v-if="isMuted(row)" :content="t('alerts.noChannelSelTip')" placement="top">
              <el-tag type="warning" size="small" style="margin: 1px">{{ t('alerts.noChannelSel') }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="categories" :label="t('alerts.categories')" min-width="170">
          <template #default="{ row }">
            <el-tag v-for="c in row.categories" :key="c" size="small" style="margin: 1px">{{ t('alerts.cat.' + c) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="min_level" :label="t('alerts.minLevel')" width="120">
          <template #default="{ row }">{{ t('common.lvl.' + row.min_level) }}</template>
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
              <el-checkbox v-for="b in formAvail.bots" :key="'im:' + b.id" :value="'im:' + b.id"
                           :disabled="b.enabled === false"
                           :title="b.enabled === false ? t('alerts.chipBotOff') : undefined">{{ b.name }}</el-checkbox>
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
            <el-option value="warn" :label="t('common.lvl.warn')" />
            <el-option value="critical" :label="t('common.lvl.critical')" />
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
import { Edit, VideoPlay, Delete, Plus } from '@element-plus/icons-vue'

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

const EMPTY_AVAIL = { email: false, sms: false, bots: [] }

const load = async () => {
  const d = await api.get('/alerts/config')
  cfg.value = d
  rows.value = d.subs
}

const availOf = (uid) => (cfg.value.users || []).find(u => u.id === uid)?.channels || EMPTY_AVAIL
const formAvail = computed(() => availOf(form.user_id))
const selHas = (row, key) => (row.channels_sel || []).includes(key)
// 全勾键集（批48：只含**可勾**通道——enabled bot+有资料 email/sms；停用/未配置不计入，
// 否则永无法全勾归一 null；onUserPicked 默认全勾/edit 回显同源）
const allKeysOf = (avail) => [
  ...(avail.email ? ['email'] : []),
  ...(avail.sms ? ['sms'] : []),
  ...(avail.bots || []).filter(b => b.enabled !== false).map(b => `im:${b.id}`),
]
const isMuted = (row) => Array.isArray(row.channels_sel) && !row.channels_sel.length
// 批48 四态统一 chips（全通道恒显）：on=会发(亮)/off=可勾未勾(灰)/disabled=未配置或停用未勾(灰)/
// on_disabled=已勾但停用(灰+✓ 勾标)——绿只留给真会发；每 chip 独立 tooltip（治批34 单 tooltip 吞 chip）
const chipsOf = (row) => {
  const avail = row.channels_avail || EMPTY_AVAIL
  const isAll = row.channels_sel === null   // null=全通道自动
  const out = []
  const pushChan = (key, dom, usable, checked) => {
    if (!usable) out.push({ key, state: 'disabled', label: t(`alerts.chip.${dom}.no`), tip: t('alerts.chipDisabled') })
    else if (isAll) out.push({ key, state: 'on', label: t(`alerts.chan.${dom}`), tip: t('alerts.allChannelsTip') })
    else out.push({ key, state: checked ? 'on' : 'off', label: t(`alerts.chan.${dom}`),
                    tip: checked ? t(`alerts.chip.${dom}.okTip`) : t('alerts.chipSkipTip') })
  }
  pushChan('email', 'email', avail.email, selHas(row, 'email'))
  pushChan('sms', 'sms', avail.sms, selHas(row, 'sms'))
  for (const b of avail.bots || []) {
    const key = 'im:' + b.id
    const checked = selHas(row, key)
    const usable = b.enabled !== false   // 批48：停用 bot 恒显
    if (isAll) out.push({ key, state: usable ? 'on' : 'disabled', label: b.name,
                          tip: usable ? t('alerts.allChannelsTip') : t('alerts.chipBotOff') })
    else if (!usable) out.push({ key, state: checked ? 'on_disabled' : 'disabled',
                                 label: (checked ? '✓ ' : '') + b.name,
                                 tip: checked ? t('alerts.chipBotOffChecked') : t('alerts.chipBotOff') })
    else out.push({ key, state: checked ? 'on' : 'off', label: b.name,
                    tip: checked ? t('alerts.chipBotOkTip') : t('alerts.chipSkipTip') })
  }
  if (!(avail.bots || []).length) out.push({ key: 'im-zero', state: 'disabled',
                                             label: t('alerts.chip.imZero'), tip: t('alerts.chip.imZeroTip') })
  return out
}

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
  } catch (e) { if (e?.detail) ElMessage.error(apiErr(e, t('common.failed'))) }
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
