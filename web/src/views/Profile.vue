<template>
  <el-card>
    <template #header>
      <!-- 批11D：个人中心三 tab（基本信息/IM 通道/修改密码） -->
      <TabsShell :tabs="tabs" default-tab="basic" query-key="ptab" v-slot="sp">
        <div v-if="sp.tab === 'basic'">
    <!-- 头像（点击即更换） -->
    <div style="display: flex; flex-direction: column; align-items: center; gap: 8px; margin-bottom: var(--sp-6)">
      <div style="cursor: pointer" @click="openChooser" :title="t('profile.clickToChange')">
        <Avatar :url="me.avatar_url" :name="me.nickname || me.username" size="lg" />
      </div>
      <div style="font-size: 16px; font-weight: bold">{{ me.nickname || me.username }}</div>
    </div>

    <!-- 资料 -->
    <el-form label-position="top" style="max-width: 480px">
      <el-form-item :label="t('profile.nickname')">
        <div style="display: flex; gap: 8px; width: 100%">
          <el-input v-model="me.nickname" maxlength="20" />
          <el-button type="primary" @click="saveNickname" :loading="savingNick">{{ t('common.save') }}</el-button>
        </div>
      </el-form-item>
      <!-- 只读字段：纯文本展示（非编辑框） -->
      <div class="info-row"><span class="info-label">{{ t('account.username') }}</span><span>{{ me.username }}</span></div>
      <div class="info-row"><span class="info-label">{{ t('user.role') }}</span><el-tag>{{ me.role }}</el-tag></div>
      <div class="info-row"><span class="info-label">{{ t('account.email') }}</span><span>{{ me.email || '-' }}</span></div>
    </el-form>

    <!-- 注销置底于基本信息（盲审 B-P2-4①） -->
    <el-divider />
    <div style="display: flex; align-items: center; justify-content: space-between">
      <span style="color: var(--text-secondary); font-size: 13px">{{ t('profile.deactivateHint') }}</span>
      <el-button type="danger" @click="onDeactivate">{{ t('profile.deactivate') }}</el-button>
    </div>
        </div>

        <div v-else-if="sp.tab === 'pwd'">
    <!-- 改密码（所有角色自助） -->
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.changePwd') }}</h3>
    <el-form label-position="top" style="max-width: 400px" @submit.prevent="onChangePwd">
      <el-form-item :label="t('account.oldPwd')">
        <el-input v-model="pwd.old_password" type="password" show-password />
      </el-form-item>
      <el-form-item :label="t('account.newPwd')">
        <el-input v-model="pwd.new_password" type="password" show-password />
      </el-form-item>
      <div class="pwd-rule">{{ t('common.passwordRule') }}</div>
      <el-form-item :label="t('register.confirmPwd')">
        <el-input v-model="pwd.confirm" type="password" show-password
          :class="{ 'mismatch': pwd.confirm && pwd.confirm !== pwd.new_password }" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onChangePwd" :loading="changingPwd">{{ t('account.changePwdBtn') }}</el-button>
      </el-form-item>
    </el-form>

        </div>

        <div v-else>
    <el-divider />

    <!-- 批11C：我的 IM 通道（owner=self；每用户可多个；消息以绑定身份继承本人组权限） -->
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
      <h3 style="font-size: 16px; margin: 0">{{ t('myIm.title') }}</h3>
      <el-button type="primary" @click="openImAdd">{{ t('myIm.add') }}</el-button>
    </div>
    <el-table v-if="imBots.length" :data="imBots" size="small">
      <el-table-column prop="provider" :label="t('myIm.provider')" width="90" />
      <el-table-column prop="name" :label="t('common.name')" min-width="120" show-overflow-tooltip />
      <el-table-column :label="t('common.status')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('myIm.pending')" width="90">
        <template #default="{ row }">
          <el-badge v-if="row.pending_binds" :value="row.pending_binds" type="warning"
                    style="cursor: pointer" @click="openBinds(row)" />
          <span v-else style="color: var(--text-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="150">
        <template #default="{ row }">
          <div style="display: inline-flex; gap: 6px">
            <el-button size="small" :type="row.enabled ? 'warning' : 'success'" @click="toggleIm(row)">
              {{ row.enabled ? t('common.stop') : t('common.start') }}
            </el-button>
            <el-button size="small" type="danger" @click="delIm(row)">{{ t('common.delete') }}</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <div v-else style="color: var(--text-secondary); font-size: 13px; margin-bottom: var(--sp-2)">{{ t('myIm.empty') }}</div>

    <!-- 添加 IM 通道（批11D：按注册表 methods 自动生成——方式选择+表单/扫码双形态） -->
    <el-dialog v-model="imAddDlg" :title="t('myIm.add')" width="520px">
      <el-form label-width="120px">
        <el-form-item :label="t('myIm.provider')">
          <el-select v-model="imForm.provider" style="width: 200px" @change="onImProviderChange">
            <el-option v-for="p in imProviders.filter(x => (x.methods||[]).length)" :key="p.provider" :value="p.provider" :label="p.provider" />
          </el-select>
        </el-form-item>
        <!-- 方式选择（多方式才显示——注册表驱动，盲审 A-P1-3 顺序无关） -->
        <el-form-item v-if="curMethods().length > 1" :label="t('myIm.method')">
          <el-radio-group v-model="imMethod" @change="onImMethodChange">
            <el-radio-button v-for="m in curMethods()" :key="m.id" :value="m.id">{{ methodLabel(m) }}</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- kind=manual：FIELD_SCHEMA 表单 -->
        <template v-if="imMethod && curMethods().find(m => m.id === imMethod)?.kind === 'manual'">
          <el-form-item :label="t('common.name')">
            <el-input v-model="imForm.name" style="width: 260px" />
          </el-form-item>
          <el-form-item v-for="f in imFields" :key="f.key" :label="te(f.label_key) ? t(f.label_key) : f.key">
            <el-input v-model="imForm.creds[f.key]" :type="f.secret ? 'password' : 'text'" show-password style="width: 260px" />
          </el-form-item>
        </template>

        <!-- kind=interactive：扫码向导 -->
        <div v-else-if="imMethod" style="padding: var(--sp-2) 0">
          <div v-if="!qrStatus" style="color: var(--text-secondary); font-size: 13px">{{ t('myIm.qrIntro') }}</div>
          <img v-if="qrImg" :src="qrImg" style="width: 220px; display: block; margin: 0 auto" alt="QR" />
          <div v-if="qrStatus === 'starting' || qrStatus === 'pending'" style="text-align: center; color: var(--text-secondary)">{{ t('myIm.qrStarting') }}</div>
          <div v-else-if="qrStatus === 'scanning'" style="text-align: center; color: var(--text-secondary); font-size: 13px">{{ t('myIm.qrScanning') }}</div>
          <div v-else-if="qrStatus === 'timeout'" style="text-align: center; color: var(--warn-fill)">{{ t('myIm.qrTimeout') }}</div>
          <div v-else-if="qrStatus === 'error'" style="text-align: center; color: var(--critical)">{{ qrNote || t('common.failed') }}</div>
          <div v-if="qrNote && qrStatus === 'done'" style="text-align: center; color: var(--warn-fill); font-size: 13px">{{ qrNote }}</div>
        </div>
      </el-form>
      <div style="color: var(--text-secondary); font-size: 12px">{{ t('myIm.addHint') }}</div>
      <template #footer>
        <el-button @click="imAddDlg = false">{{ t('common.close') }}</el-button>
        <el-button v-if="imMethod && curMethods().find(m => m.id === imMethod)?.kind === 'manual'"
                   type="primary" :loading="imSaving" @click="saveIm">{{ t('common.create') }}</el-button>
        <el-button v-else-if="imMethod && ['','timeout','error'].includes(qrStatus)"
                   type="primary" @click="startQr">{{ t('myIm.qrStart') }}</el-button>
      </template>
    </el-dialog>

    <!-- 待绑定列表（首见留痕 open_id → 一键绑定=本人身份） -->
    <el-dialog v-model="bindDlg" :title="t('myIm.bindTitle', { name: bindBot?.name || '' })" width="520px">
      <div style="color: var(--text-secondary); font-size: 13px; margin-bottom: var(--sp-2)">{{ t('myIm.bindHint') }}</div>
      <el-table :data="pendingBinds" size="small">
        <el-table-column prop="open_id" label="open_id" min-width="220" show-overflow-tooltip />
        <el-table-column prop="created_at" :label="t('common.createdAt')" width="160" />
        <el-table-column :label="t('common.action')" width="90">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="bindOpenId(row.open_id)">{{ t('myIm.bind') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

        </div>

    <!-- 选择头像弹窗：系统图标 or 上传 -->
    <el-dialog v-model="chooserVisible" :title="t('profile.chooseAvatar')" width="560px">
      <el-tabs v-model="chooserTab">
        <!-- 系统图标（36 个，点击即选） -->
        <el-tab-pane :label="t('profile.systemIcons')" name="icons">
          <div class="icon-grid">
            <img v-for="i in 36" :key="i"
              :src="`/icons/icon_${String(i - 1).padStart(2, '0')}.png`"
              :class="{ selected: selectedIcon === `icon_${String(i - 1).padStart(2, '0')}.png` }"
              @click="pickIcon(`icon_${String(i - 1).padStart(2, '0')}.png`)" />
          </div>
        </el-tab-pane>
        <!-- 上传自定义（裁剪） -->
        <el-tab-pane :label="t('profile.uploadImage')" name="upload">
          <div style="display: flex; flex-direction: column; align-items: center; gap: 12px; padding: var(--sp-2) 0">
            <el-button type="primary" @click="pickFile">{{ t('profile.pickImage') }}</el-button>
            <div v-if="rawImg" style="width: 100%; height: 300px">
              <vue-cropper ref="cropperRef" :img="rawImg" :auto-crop="true" :auto-crop-width="220" :auto-crop-height="220"
                :can-move-box="true" :fixed-box="false" :center-box="true" :info="false" output-type="jpeg" />
            </div>
            <el-button v-if="rawImg" type="primary" @click="uploadAvatar" :loading="uploading">{{ t('common.confirm') }}</el-button>
          </div>
          <input ref="fileRef" type="file" accept="image/jpeg,image/png,image/webp" style="display: none" @change="onFile" />
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
      </TabsShell>
    </template>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueCropper } from 'vue-cropper'  // 样式在 main.js 全局引入（漏引 CSS 是"界面全乱"的根因；该包 CSS 自带 scope id 与组件 __scopeId 配套自洽）
import Avatar from '../components/Avatar.vue'
import TabsShell from '../components/TabsShell.vue'
import api, { apiErr } from '../api'
import { validatePassword } from '../password'

const { t, te } = useI18n()
const tabs = [
  { key: 'basic', i18nKey: 'profile.tabBasic' },
  { key: 'im', i18nKey: 'profile.tabIm' },
  { key: 'pwd', i18nKey: 'profile.tabPwd' },
]
const router = useRouter()
const me = ref({ username: '', nickname: '', role: '', avatar_url: '', email: '' })
const chooserVisible = ref(false)
const chooserTab = ref('icons')
const selectedIcon = ref('')
const fileRef = ref(null)
const cropperRef = ref(null)
const rawImg = ref('')
const uploading = ref(false)
const savingNick = ref(false)
const pwd = ref({ old_password: '', new_password: '', confirm: '' })
const changingPwd = ref(false)

const load = async () => {
  try { me.value = { ...me.value, ...(await api.get('/user/profile')) } } catch {}
}
onMounted(load)

// ——— 批11C：我的 IM 通道（自助面——owner=本人，绑定继承本人权限） ———
const imBots = ref([])
const imProviders = ref([])
const imAddDlg = ref(false)
const imSaving = ref(false)
const imForm = ref({ provider: 'feishu', name: '', creds: {} })
const imFields = ref([])
const bindDlg = ref(false)
const bindBot = ref(null)
const pendingBinds = ref([])
const loadIm = async () => {
  try { imBots.value = await api.get('/my/im-bots') } catch {}
}
const openImAdd = async () => {
  try {
    imProviders.value = await api.get('/my/im-bots/providers')
    if (!imProviders.value.some(p => p.provider === imForm.value.provider) && imProviders.value.length)
      imForm.value.provider = imProviders.value[0].provider
  } catch {}
  imForm.value = { provider: imForm.value.provider, name: '', creds: {} }
  // 盲审 P2-4/P2-5：会话状态复位 + 默认选中首方式（单方式直进）
  stopPoll()
  qrStatus.value = ''; qrImg.value = ''; qrNote.value = ''; qrTicket.value = ''
  const ms = curMethods()
  imMethod.value = ms.length ? ms[0].id : ''
  imFields.value = ms.find(m => m.id === imMethod.value)?.fields || []
  imAddDlg.value = true
}
const saveIm = async () => {
  imSaving.value = true
  try {
    await api.post('/my/im-bots', { provider: imForm.value.provider, name: imForm.value.name,
                                    description: '', credentials: imForm.value.creds })
    ElMessage.success(t('common.createSuccess'))
    imAddDlg.value = false
    await loadIm()
  } catch (e) { ElMessage.error(apiErr(e, t('common.createFailed'))) }
  finally { imSaving.value = false }
}
const toggleIm = async (row) => {
  try { await api.post(`/my/im-bots/${row.id}/${row.enabled ? 'stop' : 'start'}`); await loadIm() }
  catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
}
const delIm = async (row) => {
  try {
    await ElMessageBox.confirm(t('myIm.delConfirm', { name: row.name }), { type: 'warning' })
    await api.delete(`/my/im-bots/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    await loadIm()
  } catch (e) { if (e === 'cancel') return; ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}
const openBinds = async (row) => {
  bindBot.value = row
  try { pendingBinds.value = await api.get(`/my/im-bots/${row.id}/pending`) } catch { pendingBinds.value = [] }
  bindDlg.value = true
}
const bindOpenId = async (openId) => {
  try {
    await api.post(`/my/im-bots/${bindBot.value.id}/bind`, { open_id: openId })
    ElMessage.success(t('myIm.bound'))
    bindDlg.value = false
    await loadIm()
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
}
onMounted(loadIm)

// ——— 批11D：扫码向导（kind=interactive 方式）+ 轮询三件套 ———
const imMethod = ref('')                     // 当前方式 id（qr|form）
const qrTicket = ref('')
const qrImg = ref('')
const qrStatus = ref('')                     // ''|starting|scanning|done|error|timeout
const qrNote = ref('')
let pollTimer = null
let qrPollFails = 0
let pollDeadline = 0
const methodLabel = m => te(m.label_key) ? t(m.label_key) : t('imBots.methodKind.' + m.kind)
const curProvider = () => imProviders.value.find(p => p.provider === imForm.value.provider)
const curMethods = () => (curProvider()?.methods || []).filter(m => m.kind === 'manual' || m.kind === 'interactive')
const onImMethodChange = () => {
  imFields.value = curMethods().find(m => m.id === imMethod.value)?.fields || []
  stopPoll()   // 换方式弃当前会话
}
const onImProviderChange = () => {   // 盲审 B-P2-8：换平台重算方式与字段（methods 空的 provider 已在下拉过滤）
  const ms = curMethods()
  imMethod.value = ms.length ? ms[0].id : ''
  onImMethodChange()
}
const startQr = async () => {
  qrStatus.value = 'starting'; qrImg.value = ''; qrNote.value = ''
  try {
    const r = await api.post(`/my/im-bots/onboarding/${imForm.value.provider}/${imMethod.value}`)
    qrTicket.value = r.ticket
    pollDeadline = Date.now() + 600_000     // 600s 前端兜底（后端 pending 混同过期永不报——B-P2-4③）
    pollTimer = setInterval(pollQr, 10_000)
  } catch (e) { qrStatus.value = 'error'; qrNote.value = apiErr(e, ''); ElMessage.error(apiErr(e, t('common.operationFailed'))) }
}
const pollQr = async () => {
  qrPollFails = 0
  if (Date.now() > pollDeadline) { qrStatus.value = 'timeout'; stopPoll(); return }
  try {
    const d = await api.get(`/my/im-bots/onboarding-status/${qrTicket.value}`)
    qrStatus.value = d.status || 'pending'
    if (d.qr_img) qrImg.value = d.qr_img
    if (d.status === 'scanning' && !qrImg.value) qrStatus.value = 'scanning'
    if (d.status === 'done') {
      stopPoll()
      if (d.owned === false) { qrNote.value = t('myIm.ownedFalse'); ElMessage.warning(t('myIm.ownedFalse')) }
      else ElMessage.success(t('myIm.qrDone'))
      await loadIm()
    }
    if (d.status === 'error') { stopPoll(); qrNote.value = d.code ? (te('err.' + d.code) ? t('err.' + d.code) : (d.error || '')) : (d.error || ''); ElMessage.error(qrNote.value || t('common.operationFailed')) }
  } catch (e) {
    // 盲审 P3：单次瞬时网络错不终止（连续 2 次才判死——后端会话可能仍活）
    qrPollFails = (qrPollFails || 0) + 1
    if (qrPollFails >= 2) { qrStatus.value = 'error'; stopPoll() }
  }
}
const stopPoll = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }
onUnmounted(stopPoll)   // 轮询三件套①（组件级）
watch(imAddDlg, v => { if (!v) stopPoll() })   // ②弹窗关停


// ——— 头像选择（点头像打开：系统图标 / 上传）———
const openChooser = () => {
  chooserTab.value = 'icons'
  chooserVisible.value = true
}
const pickIcon = async (icon) => {
  selectedIcon.value = icon
  try {
    const r = await api.post('/user/avatar', { icon })
    me.value.avatar_url = r.avatar_url
    ElMessage.success(t('profile.avatarUpdated'))
    chooserVisible.value = false
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
}
const pickFile = () => fileRef.value?.click()
const onFile = (e) => {
  const f = e.target.files?.[0]
  e.target.value = ''
  if (!f) return
  if (f.size > 2 * 1024 * 1024) { ElMessage.warning(t('profile.avatarTooLarge')); return }
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(f.type)) { ElMessage.warning(t('profile.avatarFormat')); return }
  const rd = new FileReader()
  rd.onload = ev => { rawImg.value = ev.target.result }
  rd.readAsDataURL(f)
}
const uploadAvatar = () => {
  cropperRef.value?.getCropData(async (base64) => {
    uploading.value = true
    try {
      const r = await api.post('/user/avatar', { avatar_base64: base64 })
      me.value.avatar_url = r.avatar_url
      ElMessage.success(t('profile.avatarUpdated'))
      chooserVisible.value = false
    } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
    finally { uploading.value = false }
  })
}

// ——— 昵称 / 密码 / 注销 ———
const saveNickname = async () => {
  if (!me.value.nickname?.trim()) { ElMessage.warning(t('profile.nicknameRequired')); return }
  savingNick.value = true
  try {
    await api.post('/user/profile', { nickname: me.value.nickname.trim() })
    ElMessage.success(t('common.saveSuccess'))
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { savingNick.value = false }
}

const onDeactivate = async () => {
  try {
    await ElMessageBox.confirm(t('profile.deactivateConfirm'), t('profile.deactivate'), { type: 'warning' })
    await api.post('/user/deactivate')
    localStorage.removeItem('token')
    localStorage.removeItem('role')
    router.push('/login')
  } catch (e) {
    if (e === 'cancel') return
    ElMessage.error(apiErr(e, t('common.operationFailed')))
  }
}

const onChangePwd = async () => {
  if (!pwd.value.old_password || !pwd.value.new_password) { ElMessage.warning(t('account.fillPwd')); return }
  if (!validatePassword(pwd.value.new_password)) { ElMessage.warning(t('common.passwordWeak')); return }
  if (pwd.value.new_password !== pwd.value.confirm) { ElMessage.warning(t('common.passwordMismatch')); return }
  changingPwd.value = true
  try {
    await api.post('/auth/change-password', { old_password: pwd.value.old_password, new_password: pwd.value.new_password })
    ElMessage.success(t('account.pwdChanged'))
    pwd.value = { old_password: '', new_password: '', confirm: '' }
  } catch (e) { ElMessage.error(apiErr(e, t('account.changeFailed'))) }
  finally { changingPwd.value = false }
}
</script>

<style scoped>
.mismatch :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px var(--critical) inset; }
.pwd-rule { color: var(--text-secondary); font-size: 12px; margin: -14px 0 14px; }
.info-row { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; font-size: 14px; }
.info-label { color: var(--text-secondary); min-width: 60px; }
.icon-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; max-height: 360px; overflow-y: auto; padding: 4px; }
.icon-grid img { width: 100%; aspect-ratio: 1; border-radius: 50%; cursor: pointer; border: 3px solid transparent; }
.icon-grid img:hover { border-color: var(--border-weak); }
.icon-grid img.selected { border-color: var(--brand-600); }
</style>
