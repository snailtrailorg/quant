<template>
  <div style="display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea, #764ba2)">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: var(--brand-600)">{{ t('app.title') }}</h2>
      <el-form @submit.prevent="onLogin">
        <el-form-item>
          <el-input v-model="form.username" :placeholder="t('login.username')" prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" :placeholder="t('login.password')" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%">{{ t('login.submit') }}</el-button>
      </el-form>
      <p style="text-align: center; margin-top: 12px">
        <router-link to="/forgot-password" style="color: var(--brand-600); font-size: 13px">{{ t('login.forgotPassword') }}</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { login, apiErr } from '../api'

const { t } = useI18n()
const router = useRouter()
const loading = ref(false)
const form = ref({ username: '', password: '' })

const onLogin = async () => {
  loading.value = true
  try {
    const res = await login(form.value.username, form.value.password)
    localStorage.setItem('token', res.token)
    localStorage.setItem('role', res.role)
    router.push('/')
  } catch (e) {
    ElMessage.error(apiErr(e, t('login.error')))
  } finally {
    loading.value = false
  }
}
</script>
