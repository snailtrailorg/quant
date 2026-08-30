<template>
  <div>
    <!-- 邮件发信配置（整组） -->
    <el-card style="margin-bottom: 20px">
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
          <el-input v-model="testTo" :placeholder="t('smtp.testPh')" style="width: 220px; margin-left: 8px" />
        </el-form-item>
      </el-form>
      <div style="color: #909399; font-size: 12px">{{ t('smtp.hint') }}</div>
    </el-card>

    <!-- 通用系统配置 -->
    <el-card>
      <template #header>
        <span>{{ t('systemConfig.title') }}</span>
      </template>
      <el-table :data="configs">
        <el-table-column prop="key" :label="t('common.configKey')" width="200" />
        <el-table-column :label="t('common.configValue')" width="200">
          <template #default="{ row }">
            <el-input-number v-if="row.value_type === 'int' || row.value_type === 'float'"
              v-model="row.editValue" :step="1" style="width: 140px" />
            <el-switch v-else-if="row.value_type === 'bool'" v-model="row.editValue" />
            <el-input v-else-if="row.value_type === 'password'" v-model="row.editValue" type="password" show-password
              style="width: 180px" :placeholder="row.has_value ? t('systemConfig.pwdSet') : t('systemConfig.pwdEmpty')" />
            <el-input v-else v-model="row.editValue" style="width: 180px" />
          </template>
        </el-table-column>
        <el-table-column prop="value_type" :label="t('common.type')" width="80" />
        <el-table-column prop="description" :label="t('risk.label')" />
        <el-table-column :label="t('common.updatedAt')" width="180">
          <template #default="{ row }">{{ row.updated_at }}</template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="120">
          <template #default="{ row }">
            <el-button type="primary" @click="save(row)" :loading="row._saving">{{ t('common.save') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="color: #999; font-size: 12px; margin-top: 12px">
        {{ t('systemConfig.hint') }}
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getSystemConfig, updateSystemConfig, getSmtpConfig, saveSmtpConfig, sendTestEmail, apiErr } from '../api'

const { t } = useI18n()
const configs = ref([])

// ——— 邮件发信配置（整组）———
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

const load = async () => {
  try {
    const r = await getSystemConfig()
    // smtp_* 已独立成上方整组表单，通用表中隐藏
    configs.value = (r.items || []).filter(c => !c.key.startsWith('smtp_')).map(c => {
      let editValue = c.value
      if (c.value_type === 'int') editValue = parseInt(c.value)
      else if (c.value_type === 'float') editValue = parseFloat(c.value)
      else if (c.value_type === 'bool') editValue = c.value === 'true'
      return { ...c, editValue, _saving: false }
    })
  } catch (e) { ElMessage.error(t('common.loadFailed')) }
}

const save = async (row) => {
  row._saving = true
  try {
    const res = await updateSystemConfig(row.key, row.editValue)
    if (res.dynamic) {
      const d = res.dynamic
      if (d.applied) {
        ElMessage.success(t('systemConfig.updatedDynamic', { workers: JSON.stringify(d.workers) }))
      } else {
        ElMessage.warning(t('systemConfig.updatedReason', { reason: d.reason }))
      }
    } else {
      ElMessage.success(t('systemConfig.updated'))
    }
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { row._saving = false }
}

onMounted(() => { load(); loadSmtp() })
</script>
