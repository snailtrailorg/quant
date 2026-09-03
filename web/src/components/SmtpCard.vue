<template>
  <!-- SMTP 卡(从 SystemConfig 拆出,05 §5.10:邮件属集成中心;批 1 归位重组 2026-08-30) -->
  <el-card>
    <template #header>{{ t('smtp.title') }}</template>
    <el-form :model="smtp" label-position="top" style="max-width: 560px">
      <el-row :gutter="16">
        <el-col :span="16">
          <el-form-item :label="t('smtp.host')">
            <el-input v-model="smtp.host" placeholder="smtpdm.aliyun.com / smtp.qiye.aliyun.com / smtp.qq.com" />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item :label="t('smtp.port')">
            <el-input v-model="smtp.port" placeholder="465 / 587" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item :label="t('smtp.security')">
        <el-radio-group v-model="smtp.security">
          <el-radio-button value="auto">{{ t('smtp.secAuto') }}</el-radio-button>
          <el-radio-button value="ssl">SSL (465)</el-radio-button>
          <el-radio-button value="starttls">STARTTLS (587)</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="t('smtp.username')">
        <el-input v-model="smtp.username" />
      </el-form-item>
      <el-form-item :label="t('smtp.password')">
        <el-input v-model="smtp.password" type="password" show-password
          :placeholder="smtp.password_set ? t('systemConfig.pwdSet') : t('systemConfig.pwdEmpty')" />
      </el-form-item>
      <el-form-item :label="t('smtp.from')">
        <el-input v-model="smtp.from" :placeholder="t('smtp.fromPh')" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="saveSmtp" :loading="savingSmtp">{{ t('common.save') }}</el-button>
        <el-button type="primary" @click="sendTest" :loading="testing">{{ t('smtp.test') }}</el-button>
        <el-input v-model="testTo" :placeholder="t('smtp.testPh')" style="width: 220px; margin-left: var(--sp-2)" />
      </el-form-item>
    </el-form>
    <div style="color: var(--text-secondary); font-size: 12px">{{ t('smtp.hint') }}</div>
    <el-link type="primary" @click="$router.push('/observe?tab=logs')" style="margin-top: var(--sp-2)">{{ t('smtp.viewOutbox') }} →</el-link>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getSmtpConfig, saveSmtpConfig, sendTestEmail, apiErr } from '../api'

const { t } = useI18n()
const smtp = ref({ host: '', port: '587', security: 'auto', username: '', password: '', password_set: false, from: '' })
const savingSmtp = ref(false)
const testing = ref(false)
const testTo = ref('')
const loadSmtp = async () => {
  try { smtp.value = { ...smtp.value, ...(await getSmtpConfig()), password: '' } } catch {}
}
const saveSmtp = async () => {
  savingSmtp.value = true
  try {
    await saveSmtpConfig({ host: smtp.value.host, port: smtp.value.port, security: smtp.value.security,
                           username: smtp.value.username, password: smtp.value.password, from: smtp.value.from })
    ElMessage.success(t('common.saveSuccess'))
    await loadSmtp()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { savingSmtp.value = false }
}
const sendTest = async () => {
  if (!testTo.value || !testTo.value.includes('@')) { ElMessage.warning(t('smtp.testPh')); return }
  testing.value = true
  try {
    await sendTestEmail({ to: testTo.value })
    ElMessage.success(t('smtp.testSent'))
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { testing.value = false }
}
onMounted(loadSmtp)
</script>
