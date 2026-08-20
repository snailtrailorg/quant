<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('feishu.title') }}</span>
        <el-button type="primary" @click="onConnect" :loading="loading">{{ t('feishu.addBtn') }}</el-button>
      </div>
    </template>

    <el-table :data="robots" stripe>
      <el-table-column prop="id" label="ID" width="50" />
      <el-table-column prop="name" :label="t('common.name')" width="120" />
      <el-table-column prop="app_id" label="App ID" />
      <el-table-column :label="t('feishu.sysRole')" width="90">
        <template #default="{ row }">
          <el-tag :type="roleType(row.role)">{{ roleLabel(row.role) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" :label="t('common.remark')" />
      <el-table-column :label="t('common.status')" width="70">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="340">
        <template #default="{ row }">
          <el-button type="primary" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button v-if="!row.enabled" type="success" @click="onStart(row.id)">{{ t('common.start') }}</el-button>
          <el-button v-else type="warning" @click="onStop(row.id)">{{ t('common.stop') }}</el-button>
          <el-button type="primary" @click="onSetting(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 扫码弹窗 -->
    <el-dialog v-model="scanVisible" :title="t('feishu.scanTitle')" width="360px">
      <div style="text-align: center">
        <div v-if="status === 'scanning'">
          <img v-if="qrImg" :src="qrImg" width="220" :alt="t('feishu.qrAlt')" />
          <p v-else>{{ t('feishu.qrGenerating') }}</p>
          <p style="font-size: 13px; color: #999">{{ t('feishu.validFor', { n: countdown }) }}</p>
        </div>
        <el-result v-if="status === 'done'" icon="success" :title="t('feishu.connectSuccess')" :sub-title="t('feishu.connectSuccessSub')">
          <template #extra><el-button type="primary" @click="scanVisible = false">{{ t('feishu.done') }}</el-button></template>
        </el-result>
        <el-alert v-if="status === 'error'" type="error" :title="errorMsg" show-icon :closable="false" />
      </div>
    </el-dialog>

    <!-- 设置弹窗 -->
    <el-dialog v-model="settingVisible" :title="t('feishu.settingTitle', { name: settingForm.name || '' })" width="480px">
      <el-form :model="settingForm" label-width="90px">
        <el-form-item :label="t('common.name')"><el-input v-model="settingForm.name" /></el-form-item>
        <el-form-item :label="t('feishu.sysRole')">
          <el-select v-model="settingForm.role" style="width: 100%">
            <el-option :label="t('feishu.optViewer')" value="viewer" />
            <el-option :label="t('feishu.optAnalyst')" value="analyst" />
            <el-option :label="t('feishu.optTrader')" value="trader" />
            <el-option :label="t('feishu.optAdmin')" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.remark')"><el-input v-model="settingForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSaveSetting" :loading="savingSetting">{{ t('common.confirm') }}</el-button>
          <el-button type="primary" @click="settingVisible = false">{{ t('common.cancel') }}</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getFeishuList, feishuConnect, feishuStatus, feishuStart, feishuStop, feishuDelete, feishuUpdate, testFeishu } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const robots = ref([])
const loading = ref(false)
const testing = ref(0)
const scanVisible = ref(false)
const status = ref('idle')
const qrImg = ref('')
const countdown = ref(0)
const errorMsg = ref('')
const settingVisible = ref(false)
const settingForm = ref({})
const savingSetting = ref(false)
let pollTimer = null
let cdTimer = null

const roleType = (r) => ({ admin: 'danger', trader: 'warning', analyst: 'primary', viewer: 'info' }[r] || 'info')
const roleLabel = (r) => ({ admin: 'Admin', trader: 'Trader', analyst: 'Analyst', viewer: 'Viewer' }[r] || r)

const load = async () => { robots.value = await getFeishuList() }
onMounted(load)
onUnmounted(() => { clearInterval(pollTimer); clearInterval(cdTimer) })

const onConnect = async () => {
  scanVisible.value = true
  status.value = 'scanning'
  qrImg.value = ''
  loading.value = true
  try {
    const { session_id } = await feishuConnect()
    pollTimer = setInterval(async () => {
      try {
        const r = await feishuStatus(session_id)
        if (r.status === 'scanning' && r.qr_img && !qrImg.value) {
          qrImg.value = r.qr_img
          countdown.value = r.expire_in || 600
          cdTimer = setInterval(() => { if (countdown.value > 0) countdown.value-- }, 1000)
        } else if (r.status === 'done') {
          clearInterval(pollTimer); clearInterval(cdTimer)
          status.value = 'done'
          load()
        } else if (r.status === 'error') {
          clearInterval(pollTimer); clearInterval(cdTimer)
          status.value = 'error'
          errorMsg.value = r.error || t('feishu.connectFailed')
        }
      } catch (e) { /* 忽略单次轮询失败 */ }
    }, 2000)
  } catch (e) {
    status.value = 'error'
    errorMsg.value = apiErr(e, t('feishu.connectFailed'))
  }
  finally { loading.value = false }
}

const onSetting = (row) => {
  settingForm.value = { id: row.id, name: row.name || '', role: row.role || 'viewer', description: row.description || '' }
  settingVisible.value = true
}

const onSaveSetting = async () => {
  savingSetting.value = true
  try {
    await feishuUpdate(settingForm.value.id, settingForm.value)
    ElMessage.success(t('feishu.settingUpdated'))
    settingVisible.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('feishu.updateFailed'))) }
  finally { savingSetting.value = false }
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testFeishu(id)
    if (r.ok) ElMessage.success(t('common.connectSuccess'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}

const onStart = async (id) => {
  try {
    const r = await feishuStart(id)
    if (r.ok) { ElMessage.success(t('common.started')); load() }
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { console.error(e); ElMessage.error(t('common.startFailed')) }
}

const onStop = async (id) => {
  try {
    const r = await feishuStop(id)
    if (r.ok) { ElMessage.success(t('common.stopped')); load() }
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { console.error(e); ElMessage.error(t('common.stopFailed')) }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('feishu.confirmDeleteBot'), t('common.tip'), { type: 'warning' })
  await feishuDelete(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}
</script>
