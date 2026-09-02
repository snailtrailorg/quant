<template>
  <el-card>
    <template #header>{{ t('account.manageTitle') }}</template>

    <!-- 用户管理（邀请制） -->
    <div style="margin-bottom: 20px">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
        <h3 style="font-size: 16px; margin: 0">{{ t('account.userMgmt') }}</h3>
        <el-button type="primary" @click="inviteDlg = true">{{ t('account.invite') }}</el-button>
      </div>
      <el-dialog v-model="inviteDlg" :close-on-click-modal="false" :title="t('account.invite')" width="420px">
        <el-form @submit.prevent="onInvite">
          <el-form-item>
            <el-input v-model="inviteEmail" :placeholder="t('account.phInviteEmail')" prefix-icon="Message" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="inviteDlg = false">{{ t('common.cancel') }}</el-button>
          <el-button type="primary" @click="onInvite" :loading="inviting">{{ t('account.invite') }}</el-button>
        </template>
      </el-dialog>
      <el-table :data="users" style="margin-top: 12px">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" :label="t('account.username')" min-width="100" show-overflow-tooltip />
        <el-table-column prop="nickname" :label="t('profile.nickname')" min-width="100" show-overflow-tooltip>
          <template #default="{ row }">{{ row.nickname || '-' }}</template>
        </el-table-column>
        <el-table-column prop="role" :label="t('user.role')" width="100">
          <template #default="{ row }"><el-tag>{{ row.role }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="email" :label="t('account.email')" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('account.emailVerified')" width="90">
          <template #default="{ row }">
            <el-tag :type="row.email_verified ? 'success' : 'info'">{{ row.email_verified ? '✓' : '✗' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.deactivated" type="info">{{ t('account.statusDeactivated') }}</el-tag>
            <el-tag v-else :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_login_at" :label="t('account.lastLogin')" width="150">
          <template #default="{ row }">{{ row.last_login_at || '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="270">
          <template #default="{ row }">
            <div style="display: inline-flex; gap: 6px; align-items: center; white-space: nowrap">
              <el-select v-model="row.role" style="width: 96px"
                :disabled="locked(row)" :title="lockedReason(row)"
                @change="(v) => onRoleChange(row.id, v)">
                <el-option label="Admin" value="admin" />
                <el-option label="Trader" value="trader" />
                <el-option label="Analyst" value="analyst" />
                <el-option label="Viewer" value="viewer" />
              </el-select>
              <el-button type="primary" :type="row.enabled ? 'warning' : 'success'" @click="onToggleEnabled(row)"
                :disabled="locked(row)" :title="lockedReason(row)">
                {{ row.enabled ? t('common.disable') : t('common.enable') }}
              </el-button>
              <el-button type="danger" @click="onDeleteUser(row)"
                :disabled="locked(row)" :title="lockedReason(row)">
                {{ t('common.delete') }}
              </el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 邀请记录（批次B 可观测：待注册/已用/已过期/已撤销 + 撤销） -->
    <div style="margin-bottom: 20px">
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.inviteLog') }}</h3>
      <el-table :data="invites">
        <el-table-column prop="email" :label="t('account.email')" min-width="200" show-overflow-tooltip />
        <el-table-column :label="t('common.status')" width="110">
          <template #default="{ row }">
            <el-tag :type="inviteStatusType(row.status)">{{ t('account.inviteStatus.' + row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" :label="t('common.createdAt')" width="160" />
        <el-table-column prop="expires_at" :label="t('account.inviteExpires')" width="160" />
        <el-table-column :label="t('common.action')" width="110">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending'" type="warning" @click="onRevoke(row)">{{ t('account.inviteRevoke') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-divider />

    <el-divider />

    <!-- API 密钥管理 -->
    <div>
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.apiKeys') }}</h3>
      <el-table :data="accounts">
        <el-table-column prop="name" :label="t('common.name')" min-width="150" show-overflow-tooltip />
        <el-table-column prop="exchange" :label="t('account.exchange')" width="120" />
        <el-table-column prop="api_key_hint" :label="t('account.apiKey')" show-overflow-tooltip />
        <el-table-column :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
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
import { getAccounts, getUsers, inviteUser, getMe, apiErr, getInvites, revokeInvite } from '../api'
import api from '../api'

const { t, locale } = useI18n()
const accounts = ref([])
const users = ref([])
const currentUsername = ref('')
const inviteEmail = ref('')
const inviting = ref(false)
const inviteDlg = ref(false)

// 行锁定（自己）：角色、启停、删除均禁用，title 提示原因
// （末位 admin 无需前端锁定：user_mgmt 仅 admin + 不动自己 ⇒ 末位 admin 行不可达）
const locked = row => row.username === currentUsername.value || row.deactivated
const lockedReason = row => row.username === currentUsername.value ? t('account.cantDeleteSelf') : ''

const invites = ref([])
const inviteStatusType = s => ({ pending: 'warning', used: 'success', expired: 'info', revoked: 'danger' }[s] || 'info')
const load = async () => {
  try {
    accounts.value = await getAccounts()
    users.value = await getUsers()
    invites.value = (await getInvites()).items || []
  } catch (e) { ElMessage.error(t('common.loadFailed')) }
}
const onRevoke = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.inviteRevokeConfirm', { email: row.email }), t('common.tip'), { type: 'warning' })
    await revokeInvite(row.id)
    ElMessage.success(t('account.inviteRevoked'))
    invites.value = (await getInvites()).items || []
  } catch (e) {
    if (e === 'cancel') return
    ElMessage.error(apiErr(e, t('common.operationFailed')))
  }
}
// 当前登录用户（不能删自己）
getMe().then(me => { currentUsername.value = me.username }).catch(() => {})

const onInvite = async () => {
  if (!inviteEmail.value) { ElMessage.warning(t('account.fillEmail')); return }
  inviting.value = true
  try {
    await inviteUser(inviteEmail.value, locale.value)
    ElMessage.success(t('account.inviteSent', { email: inviteEmail.value }))
    inviteEmail.value = ''; inviteDlg.value = false
    users.value = await getUsers()
  } catch (e) { ElMessage.error(apiErr(e, t('account.inviteFailed'))) }
  finally { inviting.value = false }
}

onMounted(load)

const onRoleChange = async (uid, role) => {
  try { await api.post(`/user/${uid}?role=${role}`); ElMessage.success(t('account.roleChanged')) } catch (e) { ElMessage.error(apiErr(e, t('account.roleChangeFailed'))); await load() }
}
const onToggleEnabled = async (row) => {
  try { await api.post(`/user/${row.id}?enabled=${!row.enabled}`); ElMessage.success(row.enabled ? t('common.disabled') : t('common.enabled')); await load() } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))); await load() }
}
const onDeleteUser = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.confirmDeleteUser'), { type: 'warning' })
    await api.delete(`/user/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    users.value = await getUsers()
  } catch (e) {
    if (e === 'cancel') return  // 用户取消确认
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}
</script>

<style scoped>
</style>
