<template>
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: #409eff">{{ t('forgot.title') }}</h2>
      <el-form @submit.prevent="onSubmit">
        <el-form-item>
          <el-input v-model="email" :placeholder="t('forgot.emailPlaceholder')" prefix-icon="Message" size="large" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">{{ t('forgot.submit') }}</el-button>
      </el-form>
      <p style="text-align: center; margin-top: 16px">
        <router-link to="/login" style="color: #409eff">{{ t('login.backToLogin') }}</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { forgotPassword } from '../api'

const { t } = useI18n()
const email = ref('')
const loading = ref(false)

const onSubmit = async () => {
  if (!email.value) { ElMessage.warning(t('forgot.fillEmail')); return }
  loading.value = true
  try {
    await forgotPassword(email.value)
    ElMessage.success(t('forgot.success'))
  } catch { ElMessage.error(t('forgot.failed')) }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2) }
</style>
