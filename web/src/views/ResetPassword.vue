<template>
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: #f56c6c">{{ t('reset.title') }}</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="password" type="password" :placeholder="t('reset.newPwdPlaceholder')" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">{{ t('reset.submit') }}</el-button>
      </el-form>
      <p style="text-align: center; margin-top: 16px">
        <router-link to="/login" style="color: #409eff">{{ t('login.backToLogin') }}</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { resetPassword } from '../api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const token = route.query.token
const password = ref('')
const loading = ref(false)

const onSubmit = async () => {
  if (!password.value) { ElMessage.warning(t('reset.fillNewPwd')); return }
  loading.value = true
  try {
    await resetPassword(token, password.value)
    ElMessage.success(t('reset.success'))
    router.push('/login')
  } catch (e) { ElMessage.error(e.detail || e.message || t('reset.failed')) }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2) }
</style>
