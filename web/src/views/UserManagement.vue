<template>
  <el-card>
    <template #header>
      <!-- 批11：用户管理页（系统管理第一项）——两页签：用户列表 / 用户群组（批 B 填实） -->
      <TabsShell :tabs="tabs" default-tab="users" v-slot="slotProps">
        <!-- ═══ 页签一：用户列表（=原设置·账号+邀请，列精简/两操作/批量删） ═══ -->
        <div v-if="slotProps.tab === 'users'">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
            <h3 style="font-size: 16px; margin: 0">{{ t('account.userMgmt') }}</h3>
            <el-button type="primary" @click="inviteDlg = true">{{ t('account.invite') }}</el-button>
          </div>
          <el-table :data="users" style="margin-top: 12px">
            <el-table-column prop="id" label="ID" min-width="60" />
            <el-table-column prop="username" :label="t('account.username')" min-width="120" show-overflow-tooltip />
            <!-- 批16：+昵称/邮箱（后端已返回未显示） -->
            <el-table-column prop="nickname" :label="t('cols.nickname')" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.nickname || '—' }}</template>
            </el-table-column>
            <el-table-column prop="email" :label="t('cols.email')" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.email || '—' }}</template>
            </el-table-column>
            <el-table-column prop="role" :label="t('user.role')" min-width="100">
              <template #default="{ row }"><el-tag>{{ row.role }}</el-tag></template>
            </el-table-column>
            <el-table-column :label="t('common.status')" min-width="100">
              <template #default="{ row }">
                <el-tag v-if="row.deactivated" type="info">{{ t('account.statusDeactivated') }}</el-tag>
                <el-tag v-else :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" :label="t('common.createdAt')" min-width="110">
              <template #default="{ row }">{{ (row.created_at || '').slice(0, 10) || '-' }}</template>   <!-- 到日 -->
            </el-table-column>
            <el-table-column prop="last_login_at" :label="t('account.lastLogin')" min-width="160">
              <template #default="{ row }">{{ row.last_login_at || '-' }}</template>
            </el-table-column>
            <el-table-column :label="t('common.action')" min-width="150" fixed="right">
              <template #default="{ row }">
                <div style="display: inline-flex; gap: 6px">
                  <el-button size="small" type="primary" @click="openEdit(row)"
                             :disabled="locked(row)" :title="lockedReason(row)">{{ t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" @click="onDeleteUser(row)"
                             :disabled="locked(row)" :title="lockedReason(row)">{{ t('common.delete') }}</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <!-- 编辑弹窗（角色+启停，等价原行内三操作收编） -->
          <el-dialog v-model="editDlg" :title="t('um.editUser', { name: editForm.username })" width="400px">
            <el-form label-width="90px">
              <el-form-item :label="t('user.role')">
                <el-select v-model="editForm.role" style="width: 100%">
                  <el-option v-for="g in groups" :key="g.name" :label="g.name" :value="g.name" />
                </el-select>
              </el-form-item>
              <el-form-item :label="t('common.status')">
                <el-switch v-model="editForm.enabled" :active-text="t('common.enabled')" :inactive-text="t('common.disabled')" />
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="editDlg = false">{{ t('common.cancel') }}</el-button>
              <el-button type="primary" :loading="saving" @click="onSaveEdit">{{ t('common.save') }}</el-button>
            </template>
          </el-dialog>

          <!-- 邀请记录：批量复选删除 -->
          <div style="margin-top: 28px">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
              <h3 style="font-size: 16px; margin: 0">{{ t('account.inviteLog') }}</h3>
              <el-button type="danger" plain :disabled="!inviteSel.length" @click="onBatchDelete">
                {{ t('um.batchDelete') }}{{ inviteSel.length ? ` (${inviteSel.length})` : '' }}
              </el-button>
            </div>
            <el-table :data="invites" @selection-change="s => inviteSel = s">
              <el-table-column type="selection" width="44" />
              <el-table-column prop="email" :label="t('account.email')" min-width="200" show-overflow-tooltip />
              <el-table-column :label="t('common.status')" width="110">
                <template #default="{ row }">
                  <el-tag :type="inviteStatusType(row.status)">{{ t('account.inviteStatus.' + row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" :label="t('common.createdAt')" width="160" />
              <el-table-column prop="expires_at" :label="t('account.inviteExpires')" width="160" />
              <!-- 操作列动态显示：仅存在待注册邀请时才有撤销可操作，否则整列不渲染（空壳列无意义） -->
              <el-table-column v-if="invites.some(i => i.status === 'pending')" :label="t('common.action')" width="110">
                <template #default="{ row }">
                  <el-button v-if="row.status === 'pending'" size="small" type="warning" @click="onRevoke(row)">{{ t('account.inviteRevoke') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>

          <!-- 邀请弹窗（原样搬设置页） -->
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
        </div>

        <!-- ═══ 页签二：用户群组（批11B：动态用户组——四内置锁名+自定义组增删改） ═══ -->
        <div v-else>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px">
            <h3 style="font-size: 16px; margin: 0">{{ t('um.tabGroups') }}</h3>
            <el-button type="primary" @click="openGroupEdit(null)">{{ t('um.addGroup') }}</el-button>
          </div>
          <el-table :data="groups">
            <el-table-column prop="name" :label="t('common.name')" min-width="140" />
            <el-table-column prop="description" :label="t('common.description')" min-width="200" show-overflow-tooltip />
            <el-table-column :label="t('um.groupType')" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.builtin" type="warning">builtin 🔒</el-tag>
                <el-tag v-else type="info">custom</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="user_count" :label="t('um.userCount')" width="90" />
            <el-table-column :label="t('common.action')" width="150">
              <template #default="{ row }">
                <div style="display: inline-flex; gap: 6px">
                  <el-button size="small" type="primary" @click="openGroupEdit(row)">{{ t('common.edit') }}</el-button>
                  <el-button size="small" type="danger" :disabled="row.builtin" @click="onDeleteGroup(row)">{{ t('common.delete') }}</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <!-- 组编辑弹窗（添加/编辑共用，~820px 容纳三维矩阵；新组先创建后配权限） -->
          <el-dialog v-model="groupDlg" :title="groupForm.id ? t('um.editGroup', { name: groupForm.origName }) : t('um.addGroup')" width="820px" top="4vh">
            <el-form label-width="90px" inline>
              <el-form-item :label="t('common.name')">
                <el-input v-model="groupForm.name" :disabled="groupForm.builtin" style="width: 240px"
                          :placeholder="t('um.groupNamePh')" />
              </el-form-item>
              <el-form-item :label="t('common.description')">
                <el-input v-model="groupForm.description" maxlength="200" style="width: 380px" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="groupSaving" @click="onSaveGroupInfo">
                  {{ groupForm.id ? t('common.save') : t('common.create') }}
                </el-button>
              </el-form-item>
            </el-form>
            <div v-if="groupForm.builtin" style="color: var(--text-secondary); font-size: 12px; margin: -6px 0 10px">
              {{ t('um.builtinLocked') }}
            </div>
            <el-divider style="margin: var(--sp-2) 0 var(--sp-4)" />
            <template v-if="savedName">
              <div style="font-weight: 600; margin-bottom: var(--sp-2)">{{ t('um.groupPerms') }}（{{ savedName }}）</div>
              <div style="max-height: 52vh; overflow-y: auto">
                <PermMatrix :key="savedName" :group="savedName" @saved="loadGroups" />
              </div>
            </template>
            <el-empty v-else :description="t('um.createFirst')" />
            <template #footer>
              <el-button @click="groupDlg = false">{{ t('common.close') }}</el-button>
            </template>
          </el-dialog>
        </div>
      </TabsShell>
    </template>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getUsers, getMe, getInvites, inviteUser, revokeInvite, batchDeleteInvites, apiErr } from '../api'
import api from '../api'
import TabsShell from '../components/TabsShell.vue'
import PermMatrix from '../components/PermMatrix.vue'

const { t, locale } = useI18n()
const tabs = [
  { key: 'users', i18nKey: 'um.tabUsers' },
  { key: 'groups', i18nKey: 'um.tabGroups' },
]

const users = ref([])
const invites = ref([])
const inviteSel = ref([])          // 批量复选
const currentUsername = ref('')
const inviteEmail = ref('')
const inviting = ref(false)
const inviteDlg = ref(false)

// 行锁定（自己）——同原 Account 逻辑；末位 admin 由 user_mgmt=admin-only+不动自己 隐式保证
const locked = row => row.username === currentUsername.value || row.deactivated
const lockedReason = row => row.username === currentUsername.value ? t('account.cantDeleteSelf') : ''

const inviteStatusType = s => ({ pending: 'warning', used: 'success', expired: 'info', revoked: 'danger' }[s] || 'info')

const load = async () => {
  try {
    users.value = await getUsers()
    invites.value = (await getInvites()).items || []
  } catch { ElMessage.error(t('common.loadFailed')) }
}
// —— 用户组（批11B：动态组——四内置锁名+自定义增删改；PermMatrix 配权限） ——
const groups = ref([])
const groupDlg = ref(false)
const groupSaving = ref(false)
const groupForm = ref({ id: null, name: '', description: '', builtin: false, origName: '' })
const savedName = ref('')   // 已落库组名（PermMatrix 挂载键——新组先创建、rename 先保存才有）
const loadGroups = async () => {
  try { groups.value = await api.get('/user-groups') } catch {}
}
const openGroupEdit = (row) => {
  groupForm.value = row
    ? { id: row.id, name: row.name, description: row.description || '', builtin: row.builtin, origName: row.name }
    : { id: null, name: '', description: '', builtin: false, origName: '' }
  savedName.value = row ? row.name : ''
  groupDlg.value = true
}
const onSaveGroupInfo = async () => {
  const f = groupForm.value
  groupSaving.value = true
  try {
    if (!f.id) {
      const r = await api.post('/user-groups', { name: f.name, description: f.description })
      f.id = r.id; f.origName = f.name; savedName.value = f.name
      ElMessage.success(t('common.createSuccess'))
    } else {
      await api.post(`/user-groups/${f.id}`, { name: f.name, description: f.description })
      savedName.value = f.name; f.origName = f.name
      ElMessage.success(t('common.saveSuccess'))
    }
    await Promise.all([loadGroups(), load()])   // rename 会改用户表角色显示
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { groupSaving.value = false }
}
const onDeleteGroup = async (row) => {
  try {
    await ElMessageBox.confirm(t('um.deleteGroupConfirm', { name: row.name, n: row.user_count }), { type: 'warning' })
    await api.delete(`/user-groups/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    await loadGroups()
  } catch (e) {
    if (e === 'cancel') return
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}
getMe().then(me => { currentUsername.value = me.username }).catch(() => {})
onMounted(() => { load(); loadGroups() })

// —— 编辑弹窗 ——
const editDlg = ref(false)
const saving = ref(false)
const editForm = ref({ id: 0, username: '', role: 'viewer', enabled: true })
const editOrig = ref({ role: '', enabled: true })   // 盲审 P2-9：按实际变化给文案（角色改/启停切换）
const openEdit = (row) => {
  editForm.value = { id: row.id, username: row.username, role: row.role, enabled: row.enabled }
  editOrig.value = { role: row.role, enabled: row.enabled }
  editDlg.value = true
}
const onSaveEdit = async () => {
  saving.value = true
  try {
    const f = editForm.value
    await api.post(`/user/${f.id}?role=${f.role}&enabled=${f.enabled}`)
    ElMessage.success(editOrig.value.role !== f.role ? t('account.roleChanged')
      : editOrig.value.enabled !== f.enabled ? (f.enabled ? t('common.enabled') : t('common.disabled'))
      : t('common.save') + ' ✓')
    editDlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { saving.value = false }
}

// —— 删除（确认） ——
const onDeleteUser = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.confirmDeleteUser'), { type: 'warning' })
    await api.delete(`/user/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) {
    if (e === 'cancel') return
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}

// —— 邀请 / 撤销 / 批量删 ——
const onInvite = async () => {
  if (!inviteEmail.value) { ElMessage.warning(t('account.fillEmail')); return }
  inviting.value = true
  try {
    await inviteUser(inviteEmail.value, locale.value)
    ElMessage.success(t('account.inviteSent', { email: inviteEmail.value }))
    inviteEmail.value = ''; inviteDlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('account.inviteFailed'))) }
  finally { inviting.value = false }
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
const onBatchDelete = async () => {
  const n = inviteSel.value.length
  try {
    await ElMessageBox.confirm(t('um.batchDeleteConfirm', { n }), { type: 'warning' })
    const r = await batchDeleteInvites(inviteSel.value.map(i => i.id))
    ElMessage.success(t('um.batchDeleted', { n: r.deleted ?? n }))
    await load()
  } catch (e) {
    if (e === 'cancel') return
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}
</script>
