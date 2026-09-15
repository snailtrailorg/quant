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
      <div style="font-size: var(--fs-card); font-weight: bold">{{ me.nickname || me.username }}</div>
    </div>

    <!-- 批24 迭代五（用户裁定）：编辑钮跟在值后不单列 -->
    <div class="info-table">
      <div class="info-row">
        <span class="info-label">{{ t('profile.nickname') }}</span>
        <span class="info-value">{{ me.nickname || me.username }}<IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openNickDlg" /></span>
      </div>
      <div class="info-row"><span class="info-label">{{ t('account.username') }}</span><span class="info-value">{{ me.username }}</span></div>
      <div class="info-row"><span class="info-label">{{ t('user.role') }}</span><span class="info-value"><el-tag>{{ me.role }}</el-tag></span></div>
      <div class="info-row">
        <span class="info-label">{{ t('account.email') }}</span>
        <span class="info-value">{{ me.email || '-' }}<IconBtn size="small" :icon="Edit" :title="t('emailChg.title')" @click="openEmailChg" /></span>
      </div>
      <!-- 批20 20A：三行展示（注册时间/最近登录+IP/账号状态） -->
      <div class="info-row"><span class="info-label">{{ t('profile.registeredAt') }}</span><span class="info-value">{{ me.created_at || '-' }}</span></div>
      <div class="info-row"><span class="info-label">{{ t('profile.recentLogin') }}</span><span class="info-value">{{ me.last_login_at ? `${me.last_login_at}${me.last_login_ip ? ' · ' + me.last_login_ip : ''}` : '-' }}</span></div>
      <div class="info-row"><span class="info-label">{{ t('profile.accountStatus') }}</span><span class="info-value"><el-tag type="success" size="small">{{ t('status.ok') }}</el-tag></span></div>
    </div>

    <!-- 昵称编辑弹窗（批24：行式+弹窗模式对齐邮箱） -->
    <el-dialog v-model="nickDlg" :title="t('profile.nickname')" width="420px" append-to-body :close-on-click-modal="false">
      <el-form @submit.prevent>
        <el-form-item :label="t('profile.nickname')">
          <el-input v-model="nickDraft" maxlength="20" style="width: 260px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nickDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="savingNick" @click="saveNickname">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 批20 20C：改邮箱弹窗（验证成功才改——确认邮件发新邮箱，点链接才生效） -->
    <el-dialog v-model="emailChgDlg" :title="t('emailChg.title')" width="420px" append-to-body :close-on-click-modal="false">
      <el-form label-width="130px" @submit.prevent>
        <el-form-item :label="t('emailChg.newEmail')">
          <el-input v-model="emailChgForm.new_email" type="email" />
        </el-form-item>
        <el-form-item :label="t('emailChg.password')">
          <el-input v-model="emailChgForm.current_password" type="password" show-password autocomplete="new-password" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="emailChgDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="emailChgSaving" @click="submitEmailChg">{{ t('common.submit') }}</el-button>
      </template>
    </el-dialog>

    <!-- 注销置底于基本信息（批24 迭代六：整行居中——用户裁定） -->
    <el-divider />
    <div style="display: flex; flex-direction: column; align-items: center; gap: var(--sp-2)">
      <span style="color: var(--text-secondary); font-size: var(--fs-label)">{{ t('profile.deactivateHint') }}</span>
      <el-button type="danger" @click="onDeactivate">{{ t('profile.deactivate') }}</el-button>
    </div>
        </div>

        <div v-else-if="sp.tab === 'pwd'">
    <!-- 改密码（批24 迭代五 用户裁定：不弹窗直接展示三栏，同基本信息表格化改造） -->
    <div class="info-table" style="max-width: 480px">
      <div class="info-row">
        <span class="info-label">{{ t('account.oldPwd') }}</span>
        <span class="info-value"><el-input v-model="pwd.old_password" type="password" show-password autocomplete="new-password" style="width: 260px" /></span>
      </div>
      <div class="info-row">
        <span class="info-label">{{ t('account.newPwd') }}</span>
        <span class="info-value"><el-input v-model="pwd.new_password" type="password" show-password autocomplete="new-password" style="width: 260px" /></span>
      </div>
      <div class="info-row">
        <span class="info-label">{{ t('register.confirmPwd') }}</span>
        <span class="info-value">
          <el-input v-model="pwd.confirm" type="password" show-password autocomplete="new-password"
            :class="{ 'mismatch': pwd.confirm && pwd.confirm !== pwd.new_password }" style="width: 260px" />
        </span>
      </div>
      <div class="info-row">
        <span class="info-label"></span>
        <span class="info-value">
          <span class="pwd-rule" style="flex: 1">{{ t('common.passwordRule') }}</span>
          <el-button type="primary" @click="onChangePwd" :loading="changingPwd">{{ t('account.changePwdBtn') }}</el-button>
        </span>
      </div>
    </div>

        </div>

        <div v-else-if="sp.tab === 'im'">
    <el-divider />

    <!-- 批11C：我的 IM 通道（owner=self；每用户可多个；消息以绑定身份继承本人组权限） -->
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
      <h3 style="font-size: var(--fs-card); font-weight: 600; margin: 0">{{ t('myIm.title') }}</h3>
      <IconBtn :icon="Plus" :title="t('myIm.add')" @click="openImAdd" />
    </div>
    <TableShell v-if="imBots.length" :data="imBots" size="small" storage-key="im-bots">
      <el-table-column prop="provider" :label="t('myIm.provider')" width="90" />
      <el-table-column prop="name" :label="t('common.name')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="enabled" :label="t('common.status')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="150">
        <template #default="{ row }">
          <div style="display: inline-flex; gap: 6px">
            <el-button size="small" :type="row.enabled ? 'warning' : 'success'" @click="toggleIm(row)">
              {{ row.enabled ? t('common.stop') : t('common.start') }}
            </el-button>
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="delIm(row)" />
          </div>
        </template>
      </el-table-column>
    </TableShell>
    <div v-else style="color: var(--text-secondary); font-size: var(--fs-label); margin-bottom: var(--sp-2)">{{ t('myIm.empty') }}</div>

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
        <div v-if="curPostSteps().length" style="color: var(--text-secondary); font-size: var(--fs-foot); line-height: 1.9; padding: var(--sp-2) 0">
          <div v-for="(s, i) in curPostSteps()" :key="i">{{ i + 1 }}. {{ te(s) ? t(s) : s }}</div>
        </div>
      </el-form>

      <!-- kind=interactive：扫码向导（飞书——qrSession/poll/bind 感知状态机不动，只换容器） -->
      <div v-else-if="curKind() === 'interactive'" style="padding: var(--sp-2) 0">
          <div v-if="!qrStatus" style="color: var(--text-secondary); font-size: var(--fs-label)">{{ t('myIm.qrIntro') }}</div>
          <img v-if="qrImg" :src="qrImg" style="width: 220px; display: block; margin: 0 auto" alt="QR" />
          <div v-if="qrImg && qrCountdown"
               style="text-align: center; margin-top: 6px; font-size: var(--fs-card); font-weight: 600; color: var(--brand-600)">
            {{ t('myIm.qrValid', { t: qrCountdown }) }}</div>
          <div v-if="qrStatus === 'starting' || qrStatus === 'pending'" style="text-align: center; color: var(--text-secondary)">
            <div class="qr-skeleton"></div>{{ t('myIm.qrStarting') }}
          </div>
          <div v-else-if="qrStatus === 'scanning'" style="text-align: center">
            <!-- 六轮裁定：只留「用飞书扫这个码」——时效由倒计时行表达，手机端提示不需要网页预告 -->
            <div style="color: var(--text-secondary); font-size: var(--fs-label)">{{ t('myIm.qrScanning') }}</div>
          </div>
          <div v-else-if="qrStatus === 'confirming'" style="text-align: center; color: var(--text-secondary); font-size: var(--fs-label)">{{ t('myIm.qrConfirming') }}</div>
          <div v-else-if="qrStatus === 'timeout'" style="text-align: center; color: var(--warn-fill)">{{ t('myIm.qrTimeout') }}</div>
          <div v-if="qrStatus === 'timeout'" style="text-align: center; margin-top: 8px">
            <el-button type="primary" @click="startQr">{{ t('myIm.qrRetry') }}</el-button>
          </div>
          <div v-else-if="qrStatus === 'error'" style="text-align: center; color: var(--critical)">{{ qrNote || t('common.failed') }}</div>
          <!-- 五轮：绑定机制取消——done 即成功终态（发消息即 owner 身份，无需绑定步骤） -->
          <div v-if="qrStatus === 'done'" style="text-align: center; padding: var(--sp-4) 0">
            <div style="font-size: calc(var(--fs-kpi) * 1.6); line-height: 1">✅</div>
            <div style="font-size: var(--fs-page); font-weight: 700; margin: var(--sp-2) 0 6px">{{ t('myIm.doneTitle') }}</div>
            <div style="color: var(--text-secondary); font-size: var(--fs-label)">{{ t('myIm.doneGuide') }}</div>
          </div>
          <div v-if="qrNote && qrStatus === 'done'" style="text-align: center; color: var(--warn-fill); font-size: var(--fs-label)">{{ qrNote }}</div>
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

        <div v-else>
          <!-- 批24 迭代六（用户裁定）：三分区=API 权限/菜单页面/市场操作（对齐组编辑页签序，说法统一）；
               nav 数据 /auth/me 已带（load_nav_map）——只列被限制项，未列出=可正常使用 -->
          <h3 style="font-size: var(--fs-card); font-weight: 600; margin: 0 0 12px">{{ t('perm.apiPermTitle') }}</h3>
          <el-checkbox-group v-if="permAllKeys.length" :model-value="permAllowedKeys" disabled class="perm-grid">
            <el-checkbox v-for="k in permAllKeys" :key="k" :value="k" :class="permCls(k)">
              {{ k }}
            </el-checkbox>
          </el-checkbox-group>
          <div v-else style="color: var(--text-secondary); font-size: var(--fs-label)">
            {{ t('common.noData') }}
          </div>
          <!-- 图例（批24 迭代六 文案师产出）：删内联后缀，颜色+图例承载三态语义 -->
          <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-3)">
            {{ t('perm.legendAllowed') }} · <span style="color: var(--warn-fill)">{{ t('perm.legendOrange') }}</span> · <span style="color: var(--critical)">{{ t('perm.legendRed') }}</span>
          </div>
          <el-divider />
          <h3 style="font-size: var(--fs-card); font-weight: 600; margin: 0 0 12px">{{ t('layout.navLimitTitle') }}</h3>
          <div v-if="navLimited.length" class="nav-limit-table">
            <div v-for="[id, st] in navLimited" :key="id" class="nav-limit-row">
              <span style="text-align: left">{{ navName(id) }}</span>
              <el-tag :type="st === 'hidden' ? 'danger' : 'warning'" size="small" effect="plain">
                {{ st === 'hidden' ? t('perm.navHidden') : t('perm.navReadonly') }}
              </el-tag>
            </div>
          </div>
          <div style="color: var(--text-secondary); font-size: var(--fs-foot)">
            {{ navLimited.length ? t('layout.navLimitNote') : t('layout.navLimitEmpty') }}
          </div>
          <el-divider />
          <template v-if="Object.keys(me.market_op || {}).length">
          <h3 style="font-size: var(--fs-card); font-weight: 600; margin: 0 0 12px">{{ t('profile.marketOpTitle') }}</h3>
          <el-checkbox-group :model-value="marketAllowedKeys" disabled class="perm-grid">
            <el-checkbox v-for="(ok, mk) in me.market_op || {}" :key="mk" :value="mk" :class="{ 'perm-denied': !ok }">
              {{ t('perm.mk_' + mk) }}
            </el-checkbox>
          </el-checkbox-group>
          </template>
          <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-4)">
            {{ t('layout.permChangeHint') }}
          </div>
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
import TableShell from '../components/TableShell.vue'
import api, { apiErr, sse, getMe } from '../api'
import { validatePassword } from '../password'
import IconBtn from '../components/IconBtn.vue'
import { Delete, Plus, Edit } from '@element-plus/icons-vue'

const props = defineProps({
  initialTab: { type: String, default: 'basic' },   // 深链定位（?profile=im → 'im'）
})
const emit = defineEmits(['close', 'updated'])   // updated：头像/昵称变更回传顶栏即时刷新
const { t, te } = useI18n()
const tabs = [
  { key: 'basic', i18nKey: 'profile.tabBasic' },
  { key: 'im', i18nKey: 'profile.tabIm' },
  { key: 'pwd', i18nKey: 'profile.tabPwd' },
  { key: 'perms', i18nKey: 'profile.tabPerms' },   // 批20 20B：权限概览（独立 tab——basic 塞不下，方案 v2 盲审B-P2-2）
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
const me = ref({ username: '', nickname: '', role: '', avatar_url: '', email: '',
  created_at: null, last_login_at: null, last_login_ip: null, market_op: {}, nav: {} })
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
// 批24：行式+弹窗编辑模式（昵称草稿/两弹窗开关）
const nickDlg = ref(false)
const nickDraft = ref('')
const openNickDlg = () => { nickDraft.value = me.value.nickname || ''; nickDlg.value = true }

const load = async () => {
  try { me.value = { ...me.value, ...(await api.get('/user/profile')) } } catch {}
}
onMounted(load)

// 批20 20C：改邮箱（验证成功才改——sent 后到新邮箱点链接）
const emailChgDlg = ref(false)
const emailChgSaving = ref(false)
const emailChgForm = ref({ new_email: '', current_password: '' })
const { locale } = useI18n()
const openEmailChg = () => { emailChgForm.value = { new_email: '', current_password: '' }; emailChgDlg.value = true }
const submitEmailChg = async () => {
  emailChgSaving.value = true
  try {
    await api.post('/user/email-change', { ...emailChgForm.value, lang: locale.value })
    emailChgDlg.value = false
    ElMessage.success(t('emailChg.sent', { email: emailChgForm.value.new_email }))
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { emailChgSaving.value = false }
}

// 批20 20B：权限玻璃盒（getMe 新鲜拉——meOnce 是登录时刻快照，admin 中途调组须反映，方案 v2 盲审B-P2-5）
const permBox = ref({ base: [], override: [], denied: [] })
// 批24：权限表格化只读视图——全键=三组并集；勾=有效允许（base∪override）；denied 展示但不勾（红字弱化）
const permAllKeys = computed(() => [...new Set([...permBox.value.base, ...permBox.value.override, ...permBox.value.denied])])
const permAllowedKeys = computed(() => [...permBox.value.base, ...permBox.value.override])
const permCls = k => permBox.value.denied.includes(k) ? 'perm-denied' : (permBox.value.override.includes(k) ? 'perm-ovr' : '')
const marketAllowedKeys = computed(() => Object.entries(me.value.market_op || {}).filter(([, ok]) => ok).map(([mk]) => mk))
// 批24 迭代六：菜单权限——只列被限制项（hidden/readonly），未列出=读写缺省（/auth/me 的 nav=load_nav_map 合并结果）
const navLimited = computed(() => Object.entries(me.value.nav || {}).filter(([, st]) => st && st !== 'readwrite'))
const navName = id => (te('nav.' + id) ? t('nav.' + id) : id)
onMounted(async () => {
  try {
    const me2 = await getMe()
    const perms = me2.permissions || []
    const src = me2.perm_sources || {}
    permBox.value = {
      base: perms.filter(p => (src[p] || 'role-base') === 'role-base'),
      override: perms.filter(p => src[p] === 'user-override'),
      denied: me2.denied || [],
    }
    me.value.nav = me2.nav || {}   // 批24 迭代六：菜单权限（load_nav_map 合并结果，随 getMe 新鲜拉）
  } catch {}
})

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
  if (!nickDraft.value.trim()) { ElMessage.warning(t('profile.nicknameRequired')); return }
  savingNick.value = true
  try {
    await api.post('/user/profile', { nickname: nickDraft.value.trim() })
    me.value.nickname = nickDraft.value.trim()
    emit('updated', { nickname: nickDraft.value.trim(), avatar_url: me.value.avatar_url })
    ElMessage.success(t('common.saveSuccess'))
    nickDlg.value = false
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
.pwd-rule { color: var(--text-secondary); font-size: var(--fs-foot); margin: -14px 0 14px; }
/* 批24 迭代五（用户裁定）：真·列对齐——外层单 grid 定两列轨道（label max-content/value 1fr），行 subgrid 继承；
   编辑钮不单列，直接跟在值后（value 格 inline） */
.info-table { display: grid; grid-template-columns: max-content 1fr; column-gap: var(--sp-4); row-gap: var(--sp-2); max-width: 560px; }
.info-row { display: grid; grid-template-columns: subgrid; grid-column: 1 / -1; align-items: center; padding: var(--sp-1) 0; border-bottom: 1px dashed var(--border-weak); font-size: var(--fs-body); }
.info-row:last-child { border-bottom: none; }
.info-label { text-align: right; color: var(--text-secondary); font-size: var(--fs-label); white-space: nowrap; }
/* 冒号随语言：zh 全角（对齐全站文案惯例）·en 半角——html[lang] 由 App.vue watch 同步 */
.info-label::after { content: ':'; margin-left: 2px; }
.info-label:empty::after { content: none; }   /* 批24 迭代六：空 label 不出孤立冒号（密码规则行） */
:root[lang='zh'] .info-label::after { content: '：'; }
.info-value { display: inline-flex; align-items: center; gap: 8px; text-align: left; min-width: 0; overflow-wrap: anywhere; }
.info-row { min-height: 36px; }   /* 批24 迭代六：全行等高（编辑钮 24px 与纯文本行一致） */
.nav-limit-table { max-width: 480px; margin-bottom: var(--sp-2); }
.nav-limit-row { display: grid; grid-template-columns: 1fr auto; align-items: center; padding: var(--sp-1) 0; }
/* 批24：权限只读复选框 grid（与 PermMatrix 同风格） */
.perm-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-2) var(--sp-4); }
.perm-grid :deep(.el-checkbox) { margin-right: 0; height: auto; }
.perm-denied { opacity: .55; }
.perm-denied :deep(.el-checkbox__label) { color: var(--critical); text-decoration: line-through; }
.perm-ovr :deep(.el-checkbox__label) { color: var(--warn-fill); }
.icon-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; max-height: 360px; overflow-y: auto; padding: 4px; }
.icon-grid img { width: 100%; aspect-ratio: 1; border-radius: 50%; cursor: pointer; border: 3px solid transparent; }
.icon-grid img:hover { border-color: var(--border-weak); }
.icon-grid img.selected { border-color: var(--brand-600); }
</style>
