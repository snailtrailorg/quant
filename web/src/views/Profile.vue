<template>
  <el-card style="max-width: 560px">
    <template #header>{{ t('profile.title') }}</template>

    <!-- 头像（点击即更换） -->
    <div style="display: flex; flex-direction: column; align-items: center; gap: 8px; margin-bottom: var(--sp-6)">
      <div style="cursor: pointer" @click="openChooser" :title="t('profile.clickToChange')">
        <Avatar :url="me.avatar_url" :name="me.nickname || me.username" size="lg" />
      </div>
      <div style="font-size: 16px; font-weight: bold">{{ me.nickname || me.username }}</div>
    </div>

    <!-- 资料 -->
    <el-form label-position="top">
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

    <el-divider />

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

    <el-divider />

    <!-- 注销账号（软删+脱敏，末位 admin 受限） -->
    <div style="display: flex; align-items: center; justify-content: space-between">
      <span style="color: var(--text-secondary); font-size: 13px">{{ t('profile.deactivateHint') }}</span>
      <el-button type="danger" @click="onDeactivate">{{ t('profile.deactivate') }}</el-button>
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
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VueCropper } from 'vue-cropper'  // 样式在 main.js 全局引入（漏引 CSS 是"界面全乱"的根因；该包 CSS 自带 scope id 与组件 __scopeId 配套自洽）
import Avatar from '../components/Avatar.vue'
import api, { apiErr } from '../api'
import { validatePassword } from '../password'

const { t } = useI18n()
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
