<template>
  <el-card>
    <template #header>
      <!-- 批11：用户管理页（系统管理第一项）——两页签：用户列表 / 用户群组（批 B 填实） -->
      <TabsShell :tabs="tabs" default-tab="users" v-slot="slotProps">
        <!-- ═══ 页签一：用户列表（=原设置·账号+邀请，列精简/两操作/批量删） ═══ -->
        <div v-if="slotProps.tab === 'users'">
          <!-- 批16 补齐：区块卡片层（其他多区块页均有 el-card 分节，本页原裸 div+h3——用户实测点名不一致） -->
          <el-card shadow="never" style="margin-bottom: 12px">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>{{ t('account.userMgmt') }}</span>
                <div style="display: flex; gap: 8px; align-items: center">
                  <ColumnSettings storage-key="cols.users" :columns="userColDefs" v-model:visible="userVisible" />
                </div>
              </div>
            </template>
          <TableShell :data="users" storage-key="users">
            <el-table-column v-if="userColOn('id')" prop="id" label="ID" min-width="60" />
            <el-table-column prop="username" :label="t('account.username')" min-width="120" show-overflow-tooltip />
            <!-- 批16：+昵称/邮箱（后端已返回未显示） -->
            <el-table-column v-if="userColOn('nickname')" prop="nickname" :label="t('cols.nickname')" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.nickname || '—' }}</template>
            </el-table-column>
            <el-table-column v-if="userColOn('email')" prop="email" :label="t('cols.email')" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.email || '—' }}</template>
            </el-table-column>
            <el-table-column v-if="userColOn('role')" prop="role" :label="t('user.role')" min-width="100">
              <template #default="{ row }"><el-tag>{{ row.role }}</el-tag></template>
            </el-table-column>
            <el-table-column v-if="userColOn('status')" prop="status" :label="t('common.status')" min-width="100">
              <template #default="{ row }">
                <el-tag v-if="row.deactivated" type="info">{{ t('account.statusDeactivated') }}</el-tag>
                <el-tag v-else :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="userColOn('created_at')" prop="created_at" :label="t('common.createdAt')" min-width="110">
              <template #default="{ row }">{{ (row.created_at || '').slice(0, 10) || '-' }}</template>   <!-- 到日 -->
            </el-table-column>
            <el-table-column v-if="userColOn('last_login_at')" prop="last_login_at" :label="t('account.lastLogin')" min-width="160">
              <template #default="{ row }">{{ row.last_login_at || '-' }}</template>
            </el-table-column>
            <el-table-column prop="actions" :label="t('common.action')" min-width="110" fixed="right">
              <template #default="{ row }">
                <div style="display: inline-flex; gap: 6px">
                  <!-- 批16 裁定#15：删除收编编辑弹窗（原行内双按钮）；批24 迭代十一（用户裁定）：删除回归行内图标钮 -->
                  <IconBtn size="small" :icon="Edit" :title="lockedReason(row) || t('common.edit')"
                           :disabled="locked(row)" @click="openEdit(row)" />
                  <IconBtn size="small" :icon="Delete" type="danger" :title="lockedReason(row) || t('common.delete')"
                           :disabled="locked(row)" @click="onDeleteUser(row)" />
                </div>
              </template>
            </el-table-column>
          </TableShell>
          </el-card>

          <!-- 编辑弹窗（用户名展示+用户组+启停；批24 迭代十一：email 撤出——个人中心自助改；表格化对齐同规范） -->
          <el-dialog v-model="editDlg" :title="t('um.editUser')" width="440px">
            <div class="info-table">
              <!-- 批24 迭代十（用户裁定）：title 去用户名，内容首行展示 -->
              <div class="info-row">
                <span class="info-label">{{ t('account.username') }}</span>
                <span class="info-value">{{ editForm.username }}</span>
              </div>
              <div class="info-row">
                <span class="info-label">{{ t('user.role') }}</span>
                <span class="info-value"><el-select v-model="editForm.role" style="width: 100%">   <!-- 批28-5：el-select 需显式 100%（缺省收缩到内容宽） -->
                  <el-option v-for="g in groups" :key="g.name" :label="g.name" :value="g.name" />
                </el-select></span>
              </div>
              <div class="info-row">
                <span class="info-label">{{ t('common.status') }}</span>
                <span class="info-value">
                  <el-switch v-model="editForm.enabled" :active-text="t('common.enabled')" :inactive-text="t('common.disabled')" />
                </span>
              </div>
              <!-- 批24 迭代十一（用户裁定）：email 不在管理面编辑——用户在个人中心自助改（批20 邮件验证链） -->
            </div>
            <template #footer>
              <el-button @click="editDlg = false">{{ t('common.cancel') }}</el-button>
              <el-button type="primary" :loading="saving" @click="onSaveEdit">{{ t('common.save') }}</el-button>
            </template>
          </el-dialog>

          <!-- 邀请记录（批17 17D 用户裁定：去批量选择，行级删除+撤销） -->
          <el-card shadow="never">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>{{ t('account.inviteLog') }}</span>
                <!-- 批24 用户裁定：邀请开通按钮移此（发邀请=邀请记录卡的操作，语义归位） -->
                <div style="display: flex; gap: 8px; align-items: center">
                  <IconBtn :icon="Plus" :title="t('account.invite')" @click="inviteDlg = true" />
                </div>
              </div>
            </template>
            <TableShell :data="invites" storage-key="invites">
              <el-table-column prop="email" :label="t('account.email')" min-width="200" show-overflow-tooltip />
              <el-table-column prop="status" :label="t('common.status')" min-width="100">
                <template #default="{ row }">
                  <el-tag :type="inviteStatusType(row.status)">{{ t('account.inviteStatus.' + row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" :label="t('common.createdAt')" min-width="160" />
              <el-table-column prop="expires_at" :label="t('account.inviteExpires')" min-width="160" />
              <!-- 盲审A-P1-3/B-P1-1 修：操作列恒显（原 v-if="有 pending" 是批16 仅撤销时的设计——
                   删除按钮与 pending 无关，全过期/全撤销场景下不能失去清理入口）；撤销保留行级条件 -->
              <el-table-column prop="actions" :label="t('common.action')" min-width="150">
                <template #default="{ row }">
                  <div style="display: inline-flex; gap: 6px">
                    <!-- 批28-1（用户三裁）：撤销恒显+非 pending 禁用（不隐藏）——图标 RefreshLeft→Close；措辞保留"撤销" -->
                    <IconBtn size="small" :icon="Close" type="warning" :disabled="row.status !== 'pending'"
                             :title="t('account.inviteRevoke')" @click="onRevoke(row)" />
                    <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="onDeleteInvite(row)" />
                  </div>
                </template>
              </el-table-column>
            </TableShell>
          </el-card>

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
          <!-- 批16 补齐：区块卡片层（同页签一） -->
          <el-card shadow="never">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>{{ t('um.tabGroups') }}</span>
                <IconBtn :icon="Plus" :title="t('um.addGroup')" @click="openGroupEdit(null)" />
              </div>
            </template>
            <TableShell :data="groups" storage-key="groups">
            <el-table-column prop="name" :label="t('common.name')" min-width="200" show-overflow-tooltip />
            <el-table-column prop="description" :label="t('common.description')" min-width="240" show-overflow-tooltip />
            <el-table-column prop="builtin" :label="t('um.groupType')" min-width="110">
              <template #default="{ row }">
                <el-tag v-if="row.builtin" type="warning">builtin 🔒</el-tag>
                <el-tag v-else type="info">custom</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="user_count" :label="t('um.userCount')" min-width="100" />
            <el-table-column prop="actions" :label="t('common.action')" min-width="180">
              <template #default="{ row }">
                <div style="display: inline-flex; gap: 6px">
                  <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openGroupEdit(row)" />
                  <IconBtn size="small" :icon="Delete" type="danger" :disabled="row.builtin" :title="t('common.delete')" @click="onDeleteGroup(row)" />
                </div>
              </template>
            </el-table-column>
          </TableShell>
          </el-card>

          <!-- 组编辑弹窗（添加/编辑共用；批24 迭代十二：基本信息表格化+统一保存——编辑模式单钮串基本信息+权限，
               新建模式仍"先创建后配权限"（savedName 机制约束）；弹窗 footer 只留关闭；builtin 提示行删（用户裁定：无用——锁定输入框已自明） -->
          <el-dialog v-model="groupDlg" :title="groupForm.id ? t('um.editGroup') : t('um.addGroup')" width="820px" top="4vh">
            <div class="info-table" style="max-width: 640px">
              <div class="info-row">
                <span class="info-label">{{ t('common.name') }}</span>
                <span class="info-value"><el-input v-model="groupForm.name" :disabled="groupForm.builtin"
                          :placeholder="t('um.groupNamePh')" /></span>
              </div>
              <div class="info-row">
                <span class="info-label">{{ t('common.description') }}</span>
                <span class="info-value"><el-input v-model="groupForm.description" maxlength="200" /></span>   <!-- 批28-5：去 420 定宽 -->
              </div>
            </div>
            <el-divider style="margin: var(--sp-2) 0 var(--sp-4)" />
            <template v-if="savedName">
              <div style="font-weight: 600; margin-bottom: var(--sp-2)">{{ t('um.groupPerms') }}（{{ savedName }}）</div>
              <div style="max-height: 52vh; overflow-y: auto">
                <PermMatrix ref="permMatrixRef" :key="savedName" :group="savedName" hide-save @saved="loadGroups" />
              </div>
            </template>
            <el-empty v-else :description="t('um.createFirst')" />
            <template #footer>
              <!-- 批24 迭代十四（用户裁定）：像编辑用户一样全弹窗底部两钮——取消+保存/创建（取消即关闭） -->
              <el-button @click="groupDlg = false">{{ t('common.cancel') }}</el-button>
              <el-button v-if="!groupForm.id" type="primary" :loading="groupSaving" @click="onSaveGroupInfo">
                {{ t('common.create') }}
              </el-button>
              <el-button v-else type="primary" :loading="groupSaving" @click="onSaveAll">
                {{ t('common.save') }}
              </el-button>
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
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import PermMatrix from '../components/PermMatrix.vue'
import IconBtn from '../components/IconBtn.vue'
import { Plus, Edit, Delete, Close } from '@element-plus/icons-vue'
import { computed } from 'vue'

const { t, locale } = useI18n()
// 批17 17B 补接（盲审B-P2-5）：用户表列显隐——ID/创建时间默认隐（方案 17B 圈定，扫荡分工漏接）
const userColDefs = computed(() => [
  { key: 'id', label: 'ID', hidden: true },
  { key: 'nickname', label: t('cols.nickname') },
  { key: 'email', label: t('cols.email') },
  { key: 'role', label: t('user.role') },
  { key: 'status', label: t('common.status') },
  { key: 'created_at', label: t('common.createdAt'), hidden: true },
  { key: 'last_login_at', label: t('account.lastLogin') },
])
const userVisible = ref([])
const userColOn = k => userVisible.value.includes(k)
const tabs = [
  { key: 'users', i18nKey: 'um.tabUsers' },
  { key: 'groups', i18nKey: 'um.tabGroups' },
]

const users = ref([])
const invites = ref([])
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
const permMatrixRef = ref(null)   // 批24 迭代十二：统一保存调 PermMatrix expose save
const groupSaving = ref(false)
const groupForm = ref({ id: null, name: '', description: '', builtin: false, origName: '', origDesc: '' })   // origDesc 批24 迭代十五：diff 提交基准
const savedName = ref('')   // 已落库组名（PermMatrix 挂载键——新组先创建、rename 先保存才有）
const loadGroups = async () => {
  try { groups.value = await api.get('/user-groups') } catch {}
}
const openGroupEdit = (row) => {
  groupForm.value = row
    ? { id: row.id, name: row.name, description: row.description || '', builtin: row.builtin, origName: row.name, origDesc: row.description || '' }
    : { id: null, name: '', description: '', builtin: false, origName: '', origDesc: '' }
  savedName.value = row ? row.name : ''
  groupDlg.value = true
}
const onSaveGroupInfo = async () => {
  const f = groupForm.value
  groupSaving.value = true
  try {
    if (!f.id) {
      const r = await api.post('/user-groups', { name: f.name, description: f.description })
      f.id = r.id; f.origName = f.name; f.origDesc = f.description   // 批27-20：origDesc 同步——消除后续 onSaveAll infoDirty 恒真的冗余重发
      savedName.value = f.name
      ElMessage.success(t('common.createSuccess'))
    } else {
      await api.post(`/user-groups/${f.id}`, { name: f.name, description: f.description })
      savedName.value = f.name; f.origName = f.name
      ElMessage.success(t('common.saveSuccess'))
    }
    await Promise.all([loadGroups(), load()])   // rename 会改用户表角色显示
    return true
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))); return false }
  finally { groupSaving.value = false }
}
// 批24 迭代十四：全弹窗单保存（footer）——基本信息+权限矩阵串行全链贯穿 loading；
// 矩阵 save 返回布尔（false=已各自红提示），失败不再冒矛盾的保存成功
const onSaveAll = async () => {
  const f = groupForm.value
  // 批24 迭代十五（用户裁定）：单钮多表单按 diff 提交——没改的不发请求
  const infoDirty = f.name !== f.origName || f.description !== f.origDesc
  const matrixDirty = permMatrixRef.value?.isDirty?.() ?? false
  if (!infoDirty && !matrixDirty) { groupDlg.value = false; return }   // 零修改直接关（免打扰）
  groupSaving.value = true
  try {
    if (infoDirty) {
      await api.post(`/user-groups/${f.id}`, { name: f.name, description: f.description })
      savedName.value = f.name; f.origName = f.name; f.origDesc = f.description
    }
    if (matrixDirty && await permMatrixRef.value?.save?.() === false) return
    await Promise.all([loadGroups(), load()])
    ElMessage.success(t('common.saveSuccess'))
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
    await api.post(`/user/${f.id}?role=${encodeURIComponent(f.role)}&enabled=${f.enabled}`)
    ElMessage.success(editOrig.value.role !== f.role ? t('account.roleChanged')
      : editOrig.value.enabled !== f.enabled ? (f.enabled ? t('common.enabled') : t('common.disabled'))
      : t('common.save') + ' ✓')
    editDlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { saving.value = false }
}

// —— 删除（批16 裁定#15：入口在编辑弹窗 footer；确认后弹窗随列表一并收） ——
const onDeleteUser = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.confirmDeleteUser'), { type: 'warning' })
    await api.delete(`/user/${row.id}`)
    ElMessage.success(t('common.deleteSuccess'))
    editDlg.value = false
    await load()
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
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
    if (e === 'cancel' || e === 'close') return   // 盲审B-P2-4：ESC/X reject 'close' 不算失败
    ElMessage.error(apiErr(e, t('common.operationFailed')))
  }
}
// 批17 17D：行级删除（单发 batch-delete 端点——后端收 id 数组，单元素即行删）
const onDeleteInvite = async (row) => {
  try {
    await ElMessageBox.confirm(t('account.confirmDeleteInvite', { email: row.email }), { type: 'warning' })
    await batchDeleteInvites([row.id])
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}
</script>

<style scoped>
/* 批24 迭代八：编辑弹窗表格化（与个人中心 info-table 同规范——外层轨道+行 subgrid；冒号随语言） */
.info-table { display: grid; grid-template-columns: max-content 1fr; column-gap: var(--sp-4); row-gap: var(--sp-2); }
.info-row { display: grid; grid-template-columns: subgrid; grid-column: 1 / -1; align-items: center; min-height: 36px; padding: var(--sp-1) 0; border-bottom: 1px dashed var(--border-weak); font-size: var(--fs-body); }
.info-row:last-child { border-bottom: none; }
.info-label { text-align: right; color: var(--text-secondary); font-size: var(--fs-label); white-space: nowrap; }
.info-label::after { content: ':'; margin-left: 2px; }
.info-label:empty::after { content: none; }
:root[lang='zh'] .info-label::after { content: '：'; }
.info-value { display: inline-flex; align-items: center; gap: 8px; text-align: left; min-width: 0; overflow-wrap: anywhere; }
</style>
