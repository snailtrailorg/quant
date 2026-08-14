<template>
  <el-card>
    <template #header>{{ t('account.manageTitle') }}</template>

    <!-- 用户管理（邀请制） -->
    <div style="margin-bottom: 20px">
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.userMgmt') }}</h3>
      <el-form inline @submit.prevent="onInvite">
        <el-form-item>
          <el-input v-model="inviteEmail" :placeholder="t('account.phInviteEmail')" prefix-icon="Message" style="width: 280px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onInvite" :loading="inviting">{{ t('account.invite') }}</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="users" stripe size="small" style="margin-top: 12px">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" :label="t('account.username')" width="120" />
        <el-table-column prop="role" :label="t('user.role')" width="100">
          <template #default="{ row }"><el-tag size="small">{{ row.role }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="email" :label="t('account.email')" />
        <el-table-column :label="t('account.emailVerified')" width="90">
          <template #default="{ row }">
            <el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{ row.email_verified ? '✓' : '✗' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="200">
          <template #default="{ row }">
            <el-select v-model="row.role" size="small" style="width: 90px"
              :disabled="row.username === currentUsername || (row.role === 'admin' && adminCount <= 1)"
              @change="(v) => onRoleChange(row.id, v)">
              <el-option label="Admin" value="admin" />
              <el-option label="Trader" value="trader" />
              <el-option label="Analyst" value="analyst" />
              <el-option label="Viewer" value="viewer" />
            </el-select>
            <el-button size="small" :type="row.enabled ? 'warning' : 'success'" link @click="onToggleEnabled(row)"
              :disabled="row.username === currentUsername || (row.role === 'admin' && adminCount <= 1)">
              {{ row.enabled ? t('common.disable') : t('common.enable') }}
            </el-button>
            <el-button size="small" type="danger" link @click="onDeleteUser(row)"
              :disabled="row.username === currentUsername || (row.role === 'admin' && adminCount <= 1)">
              <span v-if="row.username === currentUsername">{{ t('account.cantDeleteSelf') }}</span>
              <span v-else-if="row.role === 'admin' && adminCount <= 1">{{ t('account.cantDeleteLastAdmin') }}</span>
              <span v-else>{{ t('common.delete') }}</span>
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-divider />

    <!-- 改密码 -->
    <div style="margin-bottom: 20px">
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.changePwd') }}</h3>
      <el-form label-width="100px" style="max-width: 400px" @submit.prevent="onChangePwd">
        <el-form-item :label="t('account.oldPwd')">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item :label="t('account.newPwd')">
          <el-input v-model="pwdForm.new_password" type="password" show-password />
        </el-form-item>
        <div style="color: #909399; font-size: 12px; margin: -10px 0 10px 100px">{{ t('common.passwordRule') }}</div>
        <el-form-item :label="t('register.confirmPwd')">
          <el-input v-model="pwdForm.confirm" type="password" show-password
            :class="{ 'mismatch': pwdForm.confirm && pwdForm.confirm !== pwdForm.new_password }" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onChangePwd" :loading="changingPwd">{{ t('account.changePwdBtn') }}</el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-divider />

    <!-- API 密钥管理 -->
    <div>
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.apiKeys') }}</h3>
      <el-table :data="accounts" stripe size="small">
        <el-table-column prop="name" :label="t('common.name')" width="150" />
        <el-table-column prop="exchange" :label="t('account.exchange')" width="120" />
        <el-table-column prop="api_key_hint" :label="t('account.apiKey')" />
        <el-table-column :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        {{ t('account.apiKeyHint') }}
      </el-alert>
    </div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAccounts, getUsers, inviteUser, changePassword, getMe } from '../api'
import api from '../api'
import { validatePassword } from '../password'

const { t } = useI18n()
const accounts = ref([])
const users = ref([])
const currentUsername = ref('')
const inviteEmail = ref('')
const inviting = ref(false)
const changingPwd = ref(false)
const pwdForm = ref({ old_password: '', new_password: '', confirm: '' })

// 最后一个 admin 不能删（始终至少保留一个）
const adminCount = computed(() => users.value.filter(u => u.role === 'admin').length)

const load = async () => {
  try {
    accounts.value = await getAccounts()
    users.value = await getUsers()
  } catch (e) { ElMessage.error(t('common.loadFailed')) }
}
// 当前登录用户（不能删自己）
getMe().then(me => { currentUsername.value = me.username }).catch(() => {})

const onInvite = async () => {
  if (!inviteEmail.value) { ElMessage.warning(t('account.fillEmail')); return }
  inviting.value = true
  try {
    await inviteUser(inviteEmail.value)
    ElMessage.success(t('account.inviteSent', { email: inviteEmail.value }))
    inviteEmail.value = ''
    users.value = await getUsers()
  } catch (e) { ElMessage.error(e.detail || e.message || t('account.inviteFailed')) }
  finally { inviting.value = false }
}

const onChangePwd = async () => {
  if (!pwdForm.value.old_password || !pwdForm.value.new_password) { ElMessage.warning(t('account.fillPwd')); return }
  if (!validatePassword(pwdForm.value.new_password)) { ElMessage.warning(t('common.passwordWeak')); return }
  if (pwdForm.value.new_password !== pwdForm.value.confirm) { ElMessage.warning(t('common.passwordMismatch')); return }
  changingPwd.value = true
  try {
    await changePassword(pwdForm.value.old_password, pwdForm.value.new_password)
    ElMessage.success(t('account.pwdChanged'))
    pwdForm.value = { old_password: '', new_password: '', confirm: '' }
  } catch (e) { ElMessage.error(e.detail || e.message || t('account.changeFailed')) }
  finally { changingPwd.value = false }
}

onMounted(load)

const onRoleChange = async (uid, role) => {
  try { await api.put(`/user/${uid}?role=${role}`); ElMessage.success(t('account.roleChanged')) } catch (e) { ElMessage.error(e?.detail || t('account.roleChangeFailed')); await load() }
}
const onToggleEnabled = async (row) => {
  try { await api.put(`/user/${row.id}?enabled=${!row.enabled}`); ElMessage.success(row.enabled ? t('common.disabled') : t('common.enabled')); await load() } catch (e) { ElMessage.error(e?.detail || t('common.operationFailed')); await load() }
}
const onDeleteUser = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.confirmDeleteUser'), { type: 'warning' })
    await api.delete(`/user/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    users.value = await getUsers()
  } catch (e) {
    if (e === 'cancel') return  // 用户取消确认
    ElMessage.error(e?.detail || t('common.deleteFailed'))
  }
}
</script>

<style scoped>
.mismatch :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px #f56c6c inset; }
</style>
