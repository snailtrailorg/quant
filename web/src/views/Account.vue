<template>
  <el-card>
    <template #header>账户管理</template>

    <!-- 用户管理（邀请制） -->
    <div style="margin-bottom: 20px">
      <h3 style="font-size: 16px; margin-bottom: 12px">用户管理</h3>
      <el-form inline @submit.prevent="onInvite">
        <el-form-item>
          <el-input v-model="inviteEmail" placeholder="被邀请者邮箱" prefix-icon="Message" style="width: 280px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onInvite" :loading="inviting">邀请开通</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="users" stripe size="small" style="margin-top: 12px">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" label="用户名" width="120" />
        <el-table-column prop="role" label="角色" width="100">
          <template #default="{ row }"><el-tag size="small">{{ row.role }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="email" label="邮箱" />
        <el-table-column label="邮箱验证" width="90">
          <template #default="{ row }">
            <el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{ row.email_verified ? '✓' : '✗' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-select v-model="row.role" size="small" style="width: 90px" @change="(v) => onRoleChange(row.id, v)">
              <el-option label="Admin" value="admin" />
              <el-option label="Trader" value="trader" />
              <el-option label="Analyst" value="analyst" />
              <el-option label="Viewer" value="viewer" />
            </el-select>
            <el-button size="small" :type="row.enabled ? 'warning' : 'success'" link @click="onToggleEnabled(row)">{{ row.enabled ? '禁用' : '启用' }}</el-button>
            <el-button size="small" type="danger" link @click="onDeleteUser(row.id)" :disabled="row.username === 'admin'">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-divider />

    <!-- 改密码 -->
    <div style="margin-bottom: 20px">
      <h3 style="font-size: 16px; margin-bottom: 12px">修改密码</h3>
      <el-form label-width="100px" style="max-width: 400px" @submit.prevent="onChangePwd">
        <el-form-item label="旧密码">
          <el-input v-model="pwdForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onChangePwd" :loading="changingPwd">修改密码</el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-divider />

    <!-- API 密钥管理 -->
    <div>
      <h3 style="font-size: 16px; margin-bottom: 12px">API 密钥</h3>
      <el-table :data="accounts" stripe size="small">
        <el-table-column prop="name" label="名称" width="150" />
        <el-table-column prop="exchange" label="交易所/券商" width="120" />
        <el-table-column prop="api_key_hint" label="密钥" />
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        API 密钥加密存储，前端永不返回明文。仅 Admin 可管理。
      </el-alert>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAccounts, getUsers, inviteUser, changePassword } from '../api'
import api from '../api'

const accounts = ref([])
const users = ref([])
const inviteEmail = ref('')
const inviting = ref(false)
const changingPwd = ref(false)
const pwdForm = ref({ old_password: '', new_password: '' })

const load = async () => {
  try {
    accounts.value = await getAccounts()
    users.value = await getUsers()
  } catch (e) { ElMessage.error('加载失败') }
}

const onInvite = async () => {
  if (!inviteEmail.value) { ElMessage.warning('请填邮箱'); return }
  inviting.value = true
  try {
    await inviteUser(inviteEmail.value)
    ElMessage.success(`邀请已发送到 ${inviteEmail.value}`)
    inviteEmail.value = ''
    users.value = await getUsers()
  } catch (e) { ElMessage.error(e.detail || e.message || '邀请失败') }
  finally { inviting.value = false }
}

const onChangePwd = async () => {
  if (!pwdForm.value.old_password || !pwdForm.value.new_password) { ElMessage.warning('请填旧/新密码'); return }
  changingPwd.value = true
  try {
    await changePassword(pwdForm.value.old_password, pwdForm.value.new_password)
    ElMessage.success('密码已修改')
    pwdForm.value = { old_password: '', new_password: '' }
  } catch (e) { ElMessage.error(e.detail || e.message || '修改失败') }
  finally { changingPwd.value = false }
}

onMounted(load)

const onRoleChange = async (uid, role) => {
  try { await api.put(`/user/${uid}?role=${role}`); ElMessage.success('角色已改') } catch { ElMessage.error('改角色失败') }
}
const onToggleEnabled = async (row) => {
  try { await api.put(`/user/${row.id}?enabled=${!row.enabled}`); ElMessage.success(row.enabled ? '已禁用' : '已启用'); await load() } catch { ElMessage.error('操作失败') }
}
const onDeleteUser = async (uid) => {
  await ElMessageBox.confirm('确认删除该用户？', { type: 'warning' })
  try { await api.delete(`/user/${uid}`); ElMessage.success('已删除'); users.value = await getUsers() } catch { ElMessage.error('删除失败') }
}
</script>
