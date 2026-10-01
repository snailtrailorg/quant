<template>
  <div style="display: flex; justify-content: center; align-items: center; height: 100vh; background: var(--grad-auth)">
    <el-card style="width: 400px">
      <h2 style="text-align: center; color: var(--brand-600); font-size: var(--fs-page); font-weight: 700">{{ t('app.title') }}</h2>
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
        <router-link to="/forgot-password" style="color: var(--brand-600); font-size: var(--fs-label)">{{ t('login.forgotPassword') }}</router-link>
      </p>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { login, apiErr } from '../api'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const form = ref({ username: '', password: '' })

const onLogin = async () => {
  loading.value = true
  try {
    const res = await login(form.value.username, form.value.password)
    localStorage.setItem('token', res.token)
    localStorage.setItem('role', res.role)
    // 批76：带回跳（未登录被守卫拦下的深链目标——如 IM 解冻确认页）；默认原行为 '/'
    // 拒 `//`（协议相对 URL 形态）：vue-router push 本走同源 pushState 无跨站风险（2026-10-02 用户注记），
    // 此为纵深防御——防将来有人把 push 换成 location.href 类原生跳转时校验被绕过。
    const rd = route.query.redirect
    router.push(typeof rd === 'string' && rd.startsWith('/') && !rd.startsWith('//') ? rd : '/')
  } catch (e) {
    ElMessage.error(apiErr(e, t('login.error')))
  } finally {
    loading.value = false
  }
}
</script>
