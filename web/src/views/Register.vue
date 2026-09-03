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

        <!-- 勾选框不可直接切换：点击仅打开条款弹窗，同意状态只能由弹窗内滚到底后的确认按钮写入（强制阅读） -->
        <div class="terms-row" @click="termsVisible = true">
          <el-checkbox :model-value="agreed" />
          <span>{{ t('register.termsLabel') }}</span>
          <a href="javascript:void(0)" class="link">{{ t('register.termsLink') }}</a>
        </div>

        <el-button type="primary" native-type="submit" :loading="loading" size="large" style="width: 100%; margin-top: 12px"
          :disabled="!form.username || !form.password || !form.confirm || !agreed">{{ t('register.submit') }}</el-button>
      </el-form>

      <p class="back-login">
        <router-link to="/login" class="link">{{ t('login.backToLogin') }}</router-link>
      </p>
    </el-card>

    <!-- 条款弹窗：中英双语纵向全量，滚动到底才能确认（确认按钮始终可见） -->
    <el-dialog v-model="termsVisible" :title="t('register.termsTitle')" width="80%" top="6vh"
      @open="onTermsOpen">
      <div ref="termsScrollRef" class="terms-scroll" @scroll="onTermsScroll">
        <template v-for="(item, i) in terms" :key="item.lang">
          <h4 class="terms-lang" :style="i ? 'margin-top: var(--sp-6)' : ''">{{ item.name }}</h4>
          <pre class="terms-body">{{ item.body }}</pre>
        </template>
      </div>
      <template #footer>
        <span v-if="!termsRead" class="scroll-hint">{{ t('register.termsScrollHint') }}</span>
        <el-button type="primary" :disabled="!termsRead" @click="agreeTerms">{{ t('register.termsAgree') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { verifyInviteToken, registerUser, getTerms, apiErr } from '../api'
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
const terms = ref([])
const termsScrollRef = ref(null)
const termsRead = ref(false)  // 滚到底才 true（内容不足一屏时打开即 true）
const form = ref({ username: '', password: '', confirm: '' })

// 条款从后端 /api/terms 取（单一源，与开通邮件一致）；中英双语纵向全量展示
// 条款从后端 /api/terms 取 items [{lang,name,body}]（注册表驱动，前端不感知具体语言）
getTerms().then(r => { terms.value = r.items || [] }).catch(() => {})

// 打开弹窗重置阅读状态；内容不满一屏（无滚动条）直接视为已读
const onTermsOpen = () => {
  termsRead.value = false
  nextTick(() => {
    const el = termsScrollRef.value
    if (el && el.scrollHeight <= el.clientHeight + 4) termsRead.value = true
  })
}
const onTermsScroll = (e) => {
  const el = e.target
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 4) termsRead.value = true
}
const agreeTerms = () => { agreed.value = true; termsVisible.value = false }

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
    await registerUser(token, form.value.username, form.value.password, locale.value)
    ElMessage.success(t('register.success'))
    router.push('/login')
  } catch (e) { ElMessage.error(apiErr(e, t('register.failed'))) }
  finally { loading.value = false }
}
</script>

<style scoped>
.register-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: linear-gradient(135deg, #2b5876, #4e9e6b); padding: 40px 16px; box-sizing: border-box; }
.register-card { width: 440px; max-width: 100%; padding: 0; overflow: hidden; }
.register-card :deep(.el-card__body) { padding: 0; }
.banner { background: linear-gradient(90deg, #67c23a, #4e9e6b); color: #fff; padding: 18px 24px; font-size: 17px; font-weight: bold; }
.state-msg { padding: 32px 24px; text-align: center; color: var(--text-secondary); }
.state-msg.error { color: var(--critical); line-height: 1.8; }
.register-form { padding: 20px 28px 8px; }
.welcome { text-align: center; color: var(--text-primary); font-size: 15px; margin: 4px 0 12px; }
.email-box { background: var(--bg-canvas); border-radius: 6px; padding: 10px 14px; font-size: 13px; color: var(--text-secondary); margin-bottom: 14px; }
.pwd-rule { color: var(--text-secondary); font-size: 12px; margin: -8px 0 10px; }
.mismatch :deep(.el-input__wrapper) { box-shadow: 0 0 0 1px var(--critical) inset; }
.terms-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; cursor: pointer; user-select: none; }
.back-login { text-align: center; margin: var(--sp-2) 0 18px; }
.link { color: var(--brand-600); font-size: 13px; text-decoration: none; }
.terms-scroll { max-height: 65vh; overflow-y: auto; padding: 0 8px; }
.terms-lang { color: var(--text-primary); font-size: 16px; margin: var(--sp-4) 0 8px; }
.terms-body { white-space: pre-wrap; font-family: inherit; font-size: 16px; color: var(--text-secondary); line-height: 1.7; margin: 0; }
.scroll-hint { color: var(--warn-fill); font-size: 13px; margin-right: 12px; }
</style>
