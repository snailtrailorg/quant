<template>
  <!-- 批20 20C：改邮箱确认页（免登录——邮件链接进入，token 即凭证；验证成功才改，失败库零触碰） -->
  <div class="auth-page">
    <el-card style="width: 400px">
      <h2 style="text-align: center">{{ t('emailConfirm.title') }}</h2>
      <div v-if="state === 'verifying'" style="text-align: center; color: var(--text-secondary); padding: var(--sp-4) 0">
        {{ t('emailConfirm.verifying') }}
      </div>
      <div v-else-if="state === 'success'" style="text-align: center; padding: var(--sp-4) 0">
        <div style="font-size: calc(var(--fs-kpi) * 1.4); line-height: 1">✅</div>
        <p style="margin: var(--sp-2) 0">{{ t('emailConfirm.success') }}</p>
        <el-button type="primary" @click="$router.push('/login')">{{ t('emailConfirm.successBtn') }}</el-button>
      </div>
      <div v-else style="text-align: center; padding: var(--sp-4) 0">
        <div style="font-size: calc(var(--fs-kpi) * 1.4); line-height: 1">⚠️</div>
        <p style="margin: var(--sp-2) 0">{{ t('emailConfirm.failed') }}</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import api from '../api'

const { t } = useI18n()
const route = useRoute()
const state = ref('verifying')   // verifying | success | failed

onMounted(async () => {
  if (!route.query.token) { state.value = 'failed'; return }
  try {
    await api.post('/user/email-change/confirm', { token: route.query.token })
    state.value = 'success'
  } catch { state.value = 'failed' }
})
</script>

<style scoped>
.auth-page { display: flex; justify-content: center; align-items: center; height: 100vh; background: var(--grad-auth) }
</style>
