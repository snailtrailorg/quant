<template>
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: #409eff">开通账号</h2>
      <div v-if="verifying" style="text-align: center; color: #999">验证邀请链接...</div>
      <div v-else-if="!valid" style="text-align: center; color: #f56c6c">
        ❌ 邀请链接无效或已过期<br/>
        <router-link to="/login" style="color: #409eff">返回登录</router-link>
      </div>
      <el-form v-else @submit.prevent="onRegister">
        <p style="text-align: center; color: #666">邀请邮箱: {{ email }}</p>
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">开通账号</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { verifyInviteToken, registerUser } from '../api'

const route = useRoute()
const router = useRouter()
const token = route.query.token
const verifying = ref(true)
const valid = ref(false)
const email = ref('')
const loading = ref(false)
const form = ref({ username: '', password: '' })

onMounted(async () => {
  if (!token) { verifying.value = false; return }
  try {
    const r = await verifyInviteToken(token)
    valid.value = r.valid
    email.value = r.email
  } catch { valid.value = false }
  finally { verifying.value = false }
})

const onRegister = async () => {
  if (!form.value.username || !form.value.password) { ElMessage.warning('请填写用户名和密码'); return }
  loading.value = true
  try {
    await registerUser(token, form.value.username, form.value.password)
    ElMessage.success('开通成功，请登录')
    router.push('/login')
  } catch (e) { ElMessage.error(e.detail || e.message || '开通失败') }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2) }
</style>
