<template>
  <!-- 批16 五批：个人中心弹窗化（弹窗唯一入口——原独立路由页转 dialog 宿主；
       v-if 挂载于 MainLayout，关闭即卸载（onUnmounted 清理链全保留）） -->
  <el-dialog v-model="dlg" :title="t('profile.title')" width="720px" :before-close="onOuterClose">
    <!-- 批11D：个人中心三 tab（基本信息/IM 通道/修改密码）；批16 去路由化（routing=false 防与 ?profile= 深链互踩） -->
    <TabsShell :tabs="tabs" :default-tab="initialTab || 'basic'" :routing="false" v-slot="sp">
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
        <el-input v-model="pwd.old_password" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <el-form-item :label="t('account.newPwd')">
        <el-input v-model="pwd.new_password" type="password" show-password autocomplete="new-password" />
      </el-form-item>
      <div class="pwd-rule">{{ t('common.passwordRule') }}</div>
      <el-form-item :label="t('register.confirmPwd')">
        <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password"
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

    <!-- 添加 IM 通道（批13 页签化：页签=注册表驱动，页签内容=方式动态生成——每平台单方式；
         批16 嵌套弹窗 append-to-body） -->
    <el-dialog v-model="imAddDlg" :title="t('myIm.add')" width="520px" append-to-body :before-close="guardClose">
      <el-tabs v-model="imForm.provider" @tab-change="onImProviderChange">
        <el-tab-pane v-for="p in imProviders.filter(x => (x.methods||[]).length)" :key="p.provider"
                     :name="p.provider" :label="te('imBots.provider.' + p.provider) ? t('imBots.provider.' + p.provider) : p.provider" />
      </el-tabs>

      <!-- kind=manual：FIELD_SCHEMA 表单（钉钉/企微） -->
      <el-form v-if="curKind() === 'manual'" label-width="120px">
        <el-form-item :label="t('common.name')">
          <el-input v-model="imForm.name" style="width: 260px" />
        </el-form-item>
        <el-form-item v-for="f in imFields" :key="f.key" :label="te(f.label_key) ? t(f.label_key) : f.key">
          <el-input v-model="imForm.creds[f.key]" :type="f.secret ? 'password' : 'text'" :autocomplete="f.secret ? 'new-password' : 'off'" show-password style="width: 260px" />
        </el-form-item>
        <!-- post_steps：后台建应用指引（注册表驱动） -->
        <div v-if="curPostSteps().length" style="color: var(--text-secondary); font-size: 12px; line-height: 1.9; padding: var(--sp-2) 0">
          <div v-for="(s, i) in curPostSteps()" :key="i">{{ i + 1 }}. {{ te(s) ? t(s) : s }}</div>
        </div>
      </el-form>

      <!-- kind=interactive：扫码向导（飞书——qrSession/poll/bind 感知状态机不动，只换容器） -->
      <div v-else-if="curKind() === 'interactive'" style="padding: var(--sp-2) 0">
          <div v-if="!qrStatus" style="color: var(--text-secondary); font-size: 13px">{{ t('myIm.qrIntro') }}</div>
          <img v-if="qrImg" :src="qrImg" style="width: 220px; display: block; margin: 0 auto" alt="QR" />
          <div v-if="qrImg && qrCountdown"
               style="text-align: center; margin-top: 6px; font-size: 16px; font-weight: 600; color: var(--brand-600)">
            {{ t('myIm.qrValid', { t: qrCountdown }) }}</div>
          <div v-if="qrStatus === 'starting' || qrStatus === 'pending'" style="text-align: center; color: var(--text-secondary)">
            <div class="qr-skeleton"></div>{{ t('myIm.qrStarting') }}
          </div>
          <div v-else-if="qrStatus === 'scanning'" style="text-align: center">
            <!-- 六轮裁定：只留「用飞书扫这个码」——时效由倒计时行表达，手机端提示不需要网页预告 -->
            <div style="color: var(--text-secondary); font-size: 13px">{{ t('myIm.qrScanning') }}</div>
          </div>
          <div v-else-if="qrStatus === 'confirming'" style="text-align: center; color: var(--text-secondary); font-size: 13px">{{ t('myIm.qrConfirming') }}</div>
          <div v-else-if="qrStatus === 'timeout'" style="text-align: center; color: var(--warn-fill)">{{ t('myIm.qrTimeout') }}</div>
          <div v-if="qrStatus === 'timeout'" style="text-align: center; margin-top: 8px">
            <el-button type="primary" @click="startQr">{{ t('myIm.qrRetry') }}</el-button>
          </div>
          <div v-else-if="qrStatus === 'error'" style="text-align: center; color: var(--critical)">{{ qrNote || t('common.failed') }}</div>
          <!-- 五轮：绑定机制取消——done 即成功终态（发消息即 owner 身份，无需绑定步骤） -->
          <div v-if="qrStatus === 'done'" style="text-align: center; padding: var(--sp-4) 0">
            <div style="font-size: calc(var(--fs-kpi) * 1.6); line-height: 1">✅</div>
            <div style="font-size: var(--fs-page); font-weight: 700; margin: var(--sp-2) 0 6px">{{ t('myIm.doneTitle') }}</div>
            <div style="color: var(--text-secondary); font-size: 13px">{{ t('myIm.doneGuide') }}</div>
          </div>
          <div v-if="qrNote && qrStatus === 'done'" style="text-align: center; color: var(--warn-fill); font-size: 13px">{{ qrNote }}</div>
      </div>
      <template #footer>
        <el-button @click="guardClose(() => { imAddDlg = false })">{{ qrStatus === 'done' ? t('common.done') : t('common.cancel') }}</el-button>
        <el-button v-if="curKind() === 'manual'"
                   type="primary" :loading="imSaving" @click="saveIm">{{ t('myIm.createBtn') }}</el-button>
        <el-button v-else-if="curKind() === 'interactive' && qrStatus === 'error'"
                   type="primary" @click="startQr">{{ t('myIm.qrRetry') }}</el-button>
      </template>
    </el-dialog>


        </div>

    <!-- 选择头像弹窗：系统图标 or 上传（批16：嵌套弹窗 append-to-body——外层 dialog overflow/z-index 圈禁坑） -->
    <el-dialog v-model="chooserVisible" :title="t('profile.chooseAvatar')" width="560px" append-to-body>
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
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueCropper } from 'vue-cropper'  // 样式在 main.js 全局引入（漏引 CSS 是"界面全乱"的根因；该包 CSS 自带 scope id 与组件 __scopeId 配套自洽）
import Avatar from '../components/Avatar.vue'
import TabsShell from '../components/TabsShell.vue'
import api, { apiErr, sse } from '../api'
import { validatePassword } from '../password'

const props = defineProps({
  initialTab: { type: String, default: 'basic' },   // 深链定位（?profile=im → 'im'）
})
const emit = defineEmits(['close', 'updated'])   // updated：头像/昵称变更回传顶栏即时刷新
const { t, te } = useI18n()
const tabs = [
  { key: 'basic', i18nKey: 'profile.tabBasic' },
  { key: 'im', i18nKey: 'profile.tabIm' },
  { key: 'pwd', i18nKey: 'profile.tabPwd' },
]
const router = useRouter()
const dlg = ref(true)   // 宿主即弹窗：挂载即开；关闭经 onOuterClose 挽留闸
watch(dlg, v => { if (!v) emit('close') })
// 外层关闭挽留（批16 三坑之三）：内层扫码在途→确认；manual 表单开着→无状态直接收
const onOuterClose = (done) => {
  if (imAddDlg.value && ['scanning', 'confirming', 'starting', 'pending'].includes(qrStatus.value)) {
    ElMessageBox.confirm(t('myIm.closeGuard'), t('common.tip'), { type: 'warning' })
      .then(() => { stopPoll(); imAddDlg.value = false; done() })
      .catch(() => {})
  } else { stopPoll(); imAddDlg.value = false; done() }
}
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
const loadIm = async () => {
  try { imBots.value = await api.get('/my/im-bots') } catch {}
}
const openImAdd = async () => {
  try {
    imProviders.value = await api.get('/my/im-bots/providers')
    // 批13：默认页签=feishu（活会话唯一来源；不按字母序落 dingtalk——盲审 A-P1-3/B-P1-2；B-P2-1 显式化）
    imForm.value.provider = imProviders.value.some(p => p.provider === 'feishu')
      ? 'feishu' : (imProviders.value[0]?.provider || 'feishu')
  } catch {}
  imForm.value = { provider: imForm.value.provider, name: '', creds: {} }
  // 盲审 P2-4/P2-5：会话状态复位 + 单方式直进
  stopPoll()
  qrStatus.value = ''; qrImg.value = ''; qrNote.value = ''; qrTicket.value = ''
  const ms = curMethods()
  imMethod.value = ms.length ? ms[0].id : ''
  imFields.value = ms.find(m => m.id === imMethod.value)?.fields || []
  // 批13：恢复活会话仅在 interactive 页签可见时（盲审 A-P1-3③——manual 页签下不后台轮询飞书）
  if (curKind() === 'interactive') {
    resumeActiveSession()
    if (!qrStatus.value) startQr()   // UX 裁定：进入扫码页签直接出码（去"开始扫码"按钮层）
  }
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
const qrCountdown = ref('')
let qrTickTimer = null
const startQrCountdown = (expireAt) => {   // 批13 UX：二维码有效期倒计时（expire_in 秒）
  stopQrCountdown()
  const tick = () => {
    const left = expireAt - Date.now()
    if (left <= 0) {
      stopQrCountdown()
      // A-P2-3：expired 是端点合成态无 SSE 事件——归零即时回查一次（防兜底轮询 15s 延迟）
      if (qrTicket.value && ['scanning', 'confirming', 'starting', 'pending'].includes(qrStatus.value)) pollQr(true)
      return
    }
    qrCountdown.value = `${String(Math.floor(left / 60000)).padStart(2, '0')}:${String(Math.floor(left % 60000 / 1000)).padStart(2, '0')}`
  }
  tick()
  qrTickTimer = setInterval(tick, 1000)
}
const stopQrCountdown = () => { qrCountdown.value = ''; if (qrTickTimer) { clearInterval(qrTickTimer); qrTickTimer = null } }
const curProvider = () => imProviders.value.find(p => p.provider === imForm.value.provider)
const curMethods = () => (curProvider()?.methods || []).filter(m => m.kind === 'manual' || m.kind === 'interactive')
// 批13：每平台单方式——curKind/curPostSteps 直取首方式（页签=平台，页签内容=方式）
const curKind = () => curMethods()[0]?.kind || ''
const curPostSteps = () => curMethods()[0]?.post_steps || []
const isTerminalQr = computed(() => ['done', 'bound', 'timeout', 'error'].includes(qrStatus.value))
const onImMethodChange = () => {
  const kind = curMethods().find(m => m.id === imMethod.value)?.kind
  imFields.value = curMethods().find(m => m.id === imMethod.value)?.fields || []
  stopPoll()   // 切走停轮询（会话仍在后台——ticket 不清）
  qrStatus.value = ''; qrImg.value = ''; qrNote.value = ''   // 复位旧码区（防过期码误导；B-P1-3：qrCode 残留引用已删——未声明变量致恢复链整段死）
  // 2026-09-10 用户三轮：切回扫码=恢复活会话（不再退回"开始扫码"按钮——那要求用户重来一遍）
  if (kind === 'interactive') {
    resumeActiveSession()
    if (!qrStatus.value) startQr()   // UX 裁定：切回即出码
  }
}
const qrTicketKey = () => 'im-onboarding-ticket:' + imForm.value.provider   // B-P2-2：键绑 provider
const resumeActiveSession = () => {
  const savedTicket = sessionStorage.getItem(qrTicketKey())
  if (savedTicket) {
    qrTicket.value = savedTicket
    qrStatus.value = 'starting'   // 轮询首查即翻 scanning（有码）或 expired
    pollDeadline = Date.now() + 600_000
    startQrCountdown(pollDeadline)
    pollQr()   // 批14：立即一次+链式自续（间隔动态）
  }
}
const onImProviderChange = () => {   // 盲审 B-P2-8：换平台重算方式与字段（methods 空的 provider 已在下拉过滤）
  const ms = curMethods()
  imMethod.value = ms.length ? ms[0].id : ''
  onImMethodChange()   // 内含码区复位（P2-5）
}
const startQr = async () => {
  qrStatus.value = 'starting'; qrImg.value = ''; qrNote.value = ''
  try {
    const r = await api.post(`/my/im-bots/onboarding/${imForm.value.provider}/${imMethod.value}`)
    // 批12A：同步出码——后端 ≤5s 内直接带 qr_img（本地实测 0.69s）；202=慢网回落轮询
    qrTicket.value = r.ticket
    sessionStorage.setItem(qrTicketKey(), r.ticket)
    if (r.qr_img) { qrImg.value = r.qr_img; qrStatus.value = 'scanning' }
    pollDeadline = Date.now() + (r.expire_in || 600) * 1000
    startQrCountdown(pollDeadline)   // UX 裁定：有效期倒计时
    pollQr()   // 批14：立即一次+链式自续（间隔动态）
  } catch (e) {
    // 批12A #8：BUSY 自动恢复活会话（existing_ticket——频控困局解锁，B-P2-3 通道）
    const et = e?.existing_ticket   // 拦截器已剥 response.data（api.js L20 reject err.response?.data）
    if (et) {
      qrTicket.value = et
      sessionStorage.setItem(qrTicketKey(), et)
      qrStatus.value = 'starting'
      pollDeadline = Date.now() + 600_000
      startQrCountdown(pollDeadline)
      pollQr()   // 批14：立即一次+链式自续（间隔动态）
      ElMessage.info(t('myIm.resumed'))
      return
    }
    qrStatus.value = 'error'; qrNote.value = apiErr(e, ''); ElMessage.error(apiErr(e, t('common.operationFailed')))
  }
}
// 批14：d 的处理抽公共（pollQr 与 SSE 事件两路共用——B-P2-1 边界：只装 d 处理，
// BUSY 恢复留在 startQr catch、deadline/fails 计数留 pollQr；SSE 路径不做 deadline→timeout 翻转）
const handleOnboardingStatus = async (d) => {
  // B-P2-4：SSE 即时到+在途 poll 双达同状态——终态短路（防双 toast/双 loadIm）
  if (isTerminalQr.value && qrStatus.value === d.status) return
  // 七轮：ttl 真值校准——倒计时/pollDeadline 统一为会话真实剩余（原 startQr 用 SDK expire_in≈1h、
  // 恢复路径硬编码 10min，同一会话两个数）
  if (d.ttl && d.ttl > 0) {
    pollDeadline = Date.now() + d.ttl * 1000
    if (qrImg.value) startQrCountdown(pollDeadline)
  }
  if (d.status === 'expired') {   // 批12A（A-P2-6）：key 不存在=过期（原 pending 混同收口）
    qrStatus.value = 'timeout'; stopPoll(); sessionStorage.removeItem(qrTicketKey()); return
  }
  qrStatus.value = d.status || 'pending'
  if (d.qr_img) qrImg.value = d.qr_img
  if (d.status === 'scanning' && !qrImg.value) qrStatus.value = 'scanning'
  if (d.status === 'done') {   // 五轮：done 即成功终态（绑定取消——发消息即 owner）
    stopPoll()
    if (d.owned === false) { qrNote.value = t('myIm.ownedFalse'); ElMessage.warning(t('myIm.ownedFalse')) }
    else ElMessage.success(t('myIm.qrDone'))
    sessionStorage.removeItem(qrTicketKey())
    await loadIm()
  }
  if (d.status === 'error') { stopPoll(); qrNote.value = d.code ? (te('err.' + d.code) ? t('err.' + d.code) : (d.error || '')) : (d.error || ''); ElMessage.error(qrNote.value || t('common.operationFailed')) }
}
let pollEpoch = 0   // B-P0-1：链代数——stopPoll 递增；in-flight 的 pollQr 返回后见换代即不重挂（链复活根治）
const pollQr = async (force = false) => {
  const myEpoch = pollEpoch
  if (!force && Date.now() > pollDeadline) { qrStatus.value = 'timeout'; stopPoll(); return }
  // ↑ B-P1-2：倒计时归零的即时回查传 force（expireAt 与 pollDeadline 同源，非 force 恒被此守卫拦——契约(f)原是死代码）
  try {
    const d = await api.get(`/my/im-bots/onboarding-status/${qrTicket.value}`)
    await handleOnboardingStatus(d)
    qrPollFails = 0   // B-P2-2 死代码修：清零移到成功路径尾部（原在首行——"连续 2 次判死"从未生效）
  } catch (e) {
    // 盲审 P3：单次瞬时网络错不终止（连续 2 次才判死——后端会话可能仍活）
    qrPollFails = (qrPollFails || 0) + 1
    if (qrPollFails >= 2) { qrStatus.value = 'error'; stopPoll() }
  }
  if (myEpoch !== pollEpoch) return   // B-P0-1：期间被 stopPoll（关弹窗/终态/切页签）——不复活链
  scheduleNextPoll()   // 批14：setTimeout 链——间隔随 SSE 健康态动态（健康 15s 兜底/非健康 3s）
}
const scheduleNextPoll = () => {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  // B-P0-1：终态/弹窗关不重挂（原只查 qrTicket——done 后链复活：toast 轰炸+deadline 后 done 翻 timeout）
  if (!qrTicket.value || isTerminalQr.value || !imAddDlg.value) return
  pollTimer = setTimeout(pollQr, sse.healthy() ? 15_000 : 3_000)
}
const stopPoll = () => { pollEpoch += 1; if (pollTimer) { clearTimeout(pollTimer); pollTimer = null } }
// 批12A #4：处理中关弹窗挽留（before-close 统一 X/ESC/遮罩/footer 三路径——B-P2-1）
const guardClose = (done) => {
  // 批13：manual 页签无会话状态直接关（盲审 A-P1-3③——挽留/isTerminalQr 仅扫码页签语义）
  if (curKind() !== 'interactive') { stopPoll(); done(); return }
  if (['scanning', 'confirming', 'starting', 'pending'].includes(qrStatus.value)) {   // 盲审B-P1-1：pending（202 回落窗）也挽留
    ElMessageBox.confirm(t('myIm.closeGuard'), t('common.tip'), { type: 'warning' })
      .then(() => { stopPoll(); done() })
      .catch(() => {})
  } else { stopPoll(); done() }
}
onUnmounted(() => { stopPoll(); stopQrCountdown(); offSse?.() })   // 轮询①+倒计时+SSE 退订

// ── 批14：SSE 事件接线（组件级生命周期——弹窗/方式切换不动连接）──
// 契约：hello→连接即回查一次（初始竞态/resume 首查/重连补偿三合一——B-P0-2）；
// onboarding 事件→ticket 守卫（旧 ticket 迟到终态防误翻 A-P1-4）+弹窗开守卫
let offSse = null
offSse = sse.subscribe((ev) => {
  if (ev.event === 'hello') {
    if (!imAddDlg.value) return   // B-P2-3：弹窗已关不复活后台轮询（与数据事件同守卫）
    if (qrTicket.value && !isTerminalQr.value) pollQr()
    return
  }
  if (ev.event !== 'data') return
  const d = ev.data
  if (d?.type !== 'onboarding') return
  if (!imAddDlg.value) return                       // 弹窗已关守卫（abort 异步，in-flight 帧仍到）
  if (d.ticket !== qrTicket.value) return           // ticket 守卫
  handleOnboardingStatus(d)
})
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
    emit('updated', { nickname: me.value.nickname, avatar_url: r.avatar_url })
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
      emit('updated', { nickname: me.value.nickname, avatar_url: r.avatar_url })
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
    emit('updated', { nickname: me.value.nickname.trim(), avatar_url: me.value.avatar_url })
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
