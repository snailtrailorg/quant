<template>
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: #f56c6c">重置密码</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="新密码" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">重置密码</el-button>
      </el-form>
      <p style="text-align: center; margin-top: 16px">
        <router-link to="/login" style="color: #409eff">返回登录</router-link>
      </p>
      <p style="text-align: center; color: #999; margin-top: 16px; font-size: 12px">粤ICP备XXXX号</p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { resetPassword } from '../api'

const route = useRoute()
const router = useRouter()
const token = route.query.token
const password = ref('')
const loading = ref(false)

const onSubmit = async () => {
  if (!password.value) { ElMessage.warning('请填新密码'); return }
  loading.value = true
  try {
    await resetPassword(token, password.value)
    ElMessage.success('重置成功，请登录')
    router.push('/login')
  } catch (e) { ElMessage.error(e.detail || e.message || '重置失败') }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2) }
</style>
