<template>
  <div class="register-page">
    <el-card class="register-card">
      <!-- 顶部彩色横幅（区别于登录页的蓝紫卡片） -->
      <div class="banner">{{ t('register.bannerTitle') }}</div>

      <div v-if="verifying" class="state-msg">{{ t('register.verifying') }}</div>

      <div v-else-if="!valid" class="state-msg error">
        ❌ {{ t('register.invalid') }}<br/>
        <router-link to="/login" class="link">{{ t('login.backToLogin') }}</router-link>
      </div>

      <el-form v-else @submit.prevent="onRegister" class="register-form">
        <p class="welcome">{{ t('register.welcome', { app: t('app.title') }) }}</p>
        <div class="email-box">{{ t('register.inviteEmail') }}: <b>{{ email }}</b></div>

        <el-form-item>
          <el-input v-model="form.username" :placeholder="t('login.username')" prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" :placeholder="t('login.password')" prefix-icon="Lock" size="large" show-password />
        </el-form-item>
        <div class="pwd-rule">{{ t('common.passwordRule') }}</div>
        <el-form-item>
          <el-input v-model="form.confirm" type="password" :placeholder="t('register.confirmPwd')" prefix-icon="Lock" size="large" show-password
            :class="{ 'mismatch': form.confirm && form.confirm !== form.password }" />
        </el-form-item>

        <el-checkbox v-model="agreed" class="terms-cb">
          {{ t('register.termsLabel') }}
          <a href="javascript:void(0)" @click="termsVisible = true" class="link">{{ t('register.termsLink') }}</a>
        </el-checkbox>

        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%; margin-top: 12px"
          :disabled="!form.username || !form.password || !form.confirm || !agreed">{{ t('register.submit') }}</el-button>
      </el-form>

      <p class="back-login">
        <router-link to="/login" class="link">{{ t('login.backToLogin') }}</router-link>
      </p>
    </el-card>

    <!-- 条款弹窗 -->
    <el-dialog v-model="termsVisible" :title="t('register.termsTitle')" width="80%" top="6vh">
      <pre class="terms-body">{{ termsText }}</pre>
      <template #footer>
        <el-button type="primary" @click="termsVisible = false">{{ t('common.ok') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { verifyInviteToken, registerUser, getTerms } from '../api'
import { validatePassword } from '../password'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const token = route.query.token
const verifying = ref(true)
const valid = ref(false)
const email = ref('')
const loading = ref(false)
const agreed = ref(false)
const termsVisible = ref(false)
const termsText = ref('')
const form = ref({ username: '', password: '', confirm: '' })

// 条款从后端 /api/terms 取（单一源，与开通邮件一致），按当前语言展示
getTerms().then(r => { termsText.value = r[locale.value] || r.zh }).catch(() => {})

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
  if (!form.value.username || !form.value.password) { ElMessage.warning(t('register.fillRequired')); return }
  if (!validatePassword(form.value.password)) { ElMessage.warning(t('common.passwordWeak')); return }
  if (form.value.password !== form.value.confirm) { ElMessage.warning(t('common.passwordMismatch')); return }
  if (!agreed.value) { ElMessage.warning(t('register.agreeRequired')); return }
  loading.value = true
  try {
    await registerUser(token, form.value.username, form.value.password)
    ElMessage.success(t('register.success'))
    router.push('/login')
  } catch (e) { ElMessage.error(e.detail || e.message || t('register.failed')) }
  finally { loading.value = false }
}
</script>

<style scoped>
.register-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: linear-gradient(135deg, #2b5876, #4e9e6b); padding: 40px 16px; box-sizing: border-box; }
.register-card { width: 440px; max-width: 100%; padding: 0; overflow: hidden; }
.register-card :deep(.el-card__body) { padding: 0; }
.banner { background: linear-gradient(90deg, #67c23a, #4e9e6b); color: #fff; padding: 18px 24px; font-size: 17px; font-weight: bold; }
.state-msg { padding: 32px 24px; text-align: center; color: #999; }
.state-msg.error { color: #f56c6c; line-height: 1.8; }
.register-form { padding: 20px 28px 8px; }
.welcome { text-align: center; color: #303133; font-size: 15px; margin: 4px 0 12px; }
.email-box { background: #f5f7fa; border-radius: 6px; padding: 10px 14px; font-size: 13px; color: #606266; margin-bottom: 14px; }
.pwd-rule { color: #909399; font-size: 12px; margin: -8px 0 10px; }
.mismatch :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px #f56c6c inset; }
.terms-cb { margin-bottom: 4px; }
.back-login { text-align: center; margin: 8px 0 18px; }
.link { color: #409eff; font-size: 13px; text-decoration: none; }
.terms-body { white-space: pre-wrap; font-family: inherit; font-size: 16px; color: #606266; line-height: 1.7; margin: 0; }
</style>
