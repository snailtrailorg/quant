<template>
  <!-- 批37：短信凭证卡（从设置→告警通道弹窗迁入集成中心——凭证属集成面；端点 /api/alerts/sms-config 零动）
       批40：两 section 分区（用户裁定逻辑清晰）——基本参数 / 短信模板（两个编号+申请正文参考） -->
  <el-card>
    <template #header>{{ t('alerts.smsCred') }}</template>
    <el-form :model="form" label-position="top" style="max-width: 560px">
      <!-- ── Section 1：基本参数 ── -->
      <el-divider content-position="left">{{ t('alerts.smsSecBasic') }}</el-divider>
      <div class="sec-hint">{{ t('alerts.smsSecBasicHint') }}</div>
      <el-form-item label="AccessKey ID"><el-input v-model="form.access_key_id" :placeholder="ph()" /></el-form-item>
      <el-form-item label="AccessKey Secret"><el-input v-model="form.access_key_secret" type="password" show-password :placeholder="ph()" autocomplete="new-password" /></el-form-item>
      <el-form-item :label="t('alerts.signName')"><el-input v-model="form.sign_name" /></el-form-item>

      <!-- ── Section 2：短信模板（两个编号；申请正文从旁复制） ── -->
      <el-divider content-position="left">{{ t('alerts.smsSecTpl') }}</el-divider>
      <div class="sec-hint">{{ t('alerts.smsSecTplHint') }}</div>
      <el-form-item :label="t('alerts.tplCode')">
        <el-input v-model="form.template_code" :placeholder="t('alerts.phAlertTpl')" />
        <el-popover placement="right" :width="420" trigger="click">
          <template #reference>
            <el-link type="primary" :underline="false" style="font-size: var(--fs-foot); margin-top: 2px">{{ t('alerts.tplContentHint') }}</el-link>
          </template>
          <div class="tpl-body">{{ t('alerts.tplBodyAlert') }}</div>
          <div class="tpl-note">{{ t('alerts.tplContentNote') }}</div>
        </el-popover>
      </el-form-item>
      <el-form-item :label="t('alerts.verifyTplCode')">
        <el-input v-model="form.verify_template_code" :placeholder="t('alerts.phVerifyTpl')" />
        <el-popover placement="right" :width="420" trigger="click">
          <template #reference>
            <el-link type="primary" :underline="false" style="font-size: var(--fs-foot); margin-top: 2px">{{ t('alerts.tplContentHint') }}</el-link>
          </template>
          <div class="tpl-body">{{ t('alerts.tplBodyVerify') }}</div>
          <div class="tpl-note">{{ t('alerts.tplContentNote') }}</div>
        </el-popover>
      </el-form-item>

      <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { apiErr } from '../api'

const { t } = useI18n()
const form = ref({ access_key_id: '', access_key_secret: '', sign_name: '', template_code: '', verify_template_code: '' })
const secretSet = ref(false)
const saving = ref(false)
const ph = () => secretSet.value ? t('alerts.phKeepBlank') : ''

const load = async () => {
  try {
    const r = await api.get('/alerts/sms-config')
    secretSet.value = !!r.secret_set
    form.value = { access_key_id: '', access_key_secret: '',
                   sign_name: r.sign_name || '', template_code: r.template_code || '',
                   verify_template_code: r.verify_template_code || '' }
  } catch { /* 权限/网络失败静默——保存时显错 */ }
}
onMounted(load)

const save = async () => {
  saving.value = true
  try {
    await api.put('/alerts/sms-config', form.value)
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
</script>
<style scoped>
.sec-hint { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin: -4px 0 12px; }
.tpl-body { font-family: var(--font-mono, monospace); font-size: var(--fs-foot); padding: 4px; word-break: break-all;
            background: var(--fill-weak, transparent); border-radius: 4px; }
.tpl-note { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: 6px; line-height: 1.5; }
</style>
