<template>
  <div v-if="!allowed" class="empty-cell" style="padding: 24px 0">{{ t('alerts.noPerm') }}</div>
  <div v-else>
    <el-alert v-if="!cfg.sms_configured" type="info" :closable="false" style="margin-bottom: 12px">
      {{ t('alerts.smsNotConfigured') }}
    </el-alert>

    <el-row :gutter="16">
      <el-col v-for="ch in ['im','email','sms']" :key="ch" :xs="24" :md="8">
        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header>
            <div style="display:flex; justify-content:space-between; align-items:center">
              <b>{{ t('alerts.channel.' + ch) }}</b>
              <el-switch v-model="form[ch].enabled" />
            </div>
          </template>
          <el-form label-width="90px" label-position="top">
            <el-form-item :label="t('alerts.target')">
              <el-select v-if="ch === 'im'" v-model="form[ch].target" style="width:100%" :disabled="!form[ch].enabled">
                <el-option v-for="b in cfg.im_bots" :key="b.id" :value="String(b.id)"
                           :label="`${b.name}（${t('alerts.channel.' + b.provider)} · ${t('alerts.bound')}: ${b.bound_users}）`"
                           :disabled="!b.enabled" />
              </el-select>
              <el-input v-else-if="ch === 'email'" v-model="form[ch].target"
                        :placeholder="t('alerts.phEmail')" :disabled="!form[ch].enabled" />
              <el-input v-else v-model="form[ch].target"
                        :placeholder="t('alerts.phPhone')" :disabled="!form[ch].enabled" />
            </el-form-item>
            <el-form-item v-if="ch === 'im' && selBot && selBot.bound_users === 0" :label="''">
              <span style="color: var(--warn); font-size: var(--fs-foot)">{{ t('alerts.noBinding') }}</span>
            </el-form-item>
            <el-form-item :label="t('alerts.categories')">
              <el-checkbox-group v-model="form[ch].categories" :disabled="!form[ch].enabled">
                <el-checkbox v-for="c in CATS" :key="c" :value="c">{{ t('alerts.cat.' + c) }}</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item :label="t('alerts.minLevel')">
              <el-select v-model="form[ch].min_level" style="width:100%" :disabled="!form[ch].enabled">
                <el-option value="warn" :label="t('alerts.lvl.warn')" />
                <el-option value="critical" :label="t('alerts.lvl.critical')" />
              </el-select>
            </el-form-item>
          </el-form>
          <div style="display:flex; justify-content:space-between; align-items:center">
            <span style="color: var(--text-secondary); font-size: var(--fs-foot)">
              {{ t('alerts.quota') }}: {{ cfg.quota[ch] || '—' }}/d
            </span>
            <el-button size="small" type="primary" :disabled="!form[ch].enabled || testing[ch] || dirty"
                       :title="dirty ? t('alerts.saveFirst') : ''" @click="test(ch)">
              {{ t('alerts.testBtn') }}
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <div style="display:flex; gap: 8px">
      <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      <el-button v-if="isAdmin" type="warning" @click="smsDlg = true">{{ t('alerts.smsCred') }}</el-button>
    </div>

    <!-- 短信凭证（专用端点,secret 只写不读） -->
    <el-dialog v-model="smsDlg" :title="t('alerts.smsCred')" width="480px">
      <el-form label-width="140px">
        <el-form-item label="AccessKey ID"><el-input v-model="smsForm.access_key_id" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item label="AccessKey Secret"><el-input v-model="smsForm.access_key_secret" type="password" show-password :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.signName')"><el-input v-model="smsForm.sign_name" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
        <el-form-item :label="t('alerts.tplCode')"><el-input v-model="smsForm.template_code" :placeholder="t('alerts.phKeepBlank')" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="smsDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveSms">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'

const { t } = useI18n()
const CATS = ['risk', 'task', 'data', 'system']
const allowed = ref(false)
const isAdmin = localStorage.getItem('role') === 'admin'
const cfg = ref({ channels: [], sms_configured: false, im_bots: [], quota: {} })
const form = reactive({
  im: { enabled: false, target: '', categories: ['risk', 'system'], min_level: 'warn' },
  email: { enabled: false, target: '', categories: ['risk'], min_level: 'warn' },
  sms: { enabled: false, target: '', categories: ['risk'], min_level: 'critical' },   // B3-14:默认 critical 防烧配额
})
const saving = ref(false)
const testing = reactive({ im: false, email: false, sms: false })
const smsDlg = ref(false)
const smsForm = ref({ access_key_id: '', access_key_secret: '', sign_name: '', template_code: '' })

let pristine = ''
const dirty = computed(() => JSON.stringify(form) !== pristine)

const selBot = computed(() => {
  const b = cfg.value.im_bots.find(x => String(x.id) === form.im.target)
  return b && b.enabled ? b : null
})

const load = async () => {
  const d = await api.get('/alerts/config')
  cfg.value = d
  for (const r of d.channels) {
    if (form[r.channel]) {
      form[r.channel] = { enabled: r.enabled, target: r.target || '',
                          categories: r.categories || [], min_level: r.min_level || 'warn' }
    }
  }
  pristine = JSON.stringify(form)
}

const save = async () => {
  saving.value = true
  try {
    await api.put('/alerts/config', { channels: ['im', 'email', 'sms'].map(ch => ({ ...form[ch], channel: ch })) })
    ElMessage.success(t('common.success'))
    await load()
    pristine = JSON.stringify(form)
  } catch (e) {
    ElMessage.error(e?.detail || t('common.failed'))
  } finally { saving.value = false }
}

const saveSms = async () => {
  try {
    await api.put('/alerts/sms-config', smsForm.value)
    smsDlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(e?.detail || t('common.failed')) }
}

const test = async (ch) => {
  testing[ch] = true
  try {
    const r = await api.post(`/alerts/test`, { channel: ch })
    r.ok ? ElMessage.success(`${t('alerts.channel.' + ch)}: ${r.detail}`) : ElMessage.warning(`${t('alerts.channel.' + ch)}: ${r.detail}`)
  } catch (e) {
    ElMessage.error(e?.detail || t('common.failed'))
  } finally { testing[ch] = false }
}

onMounted(async () => {
  try {
    const me = await api.get('/auth/me')
    allowed.value = (me.permissions || []).includes('alerts_config')
  } catch { allowed.value = isAdmin }
  if (allowed.value) await load().catch(() => {})
})
</script>
