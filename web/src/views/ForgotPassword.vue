<template>
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: #409eff">找回密码</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="email" placeholder="注册邮箱" prefix-icon="Message" size="large" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">发送重置邮件</el-button>
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
import { ElMessage } from 'element-plus'
import { forgotPassword } from '../api'

const email = ref('')
const loading = ref(false)

const onSubmit = async () => {
  if (!email.value) { ElMessage.warning('请填邮箱'); return }
  loading.value = true
  try {
    await forgotPassword(email.value)
    ElMessage.success('重置邮件已发送（如果邮箱存在）')
  } catch { ElMessage.error('发送失败') }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2) }
</style>
