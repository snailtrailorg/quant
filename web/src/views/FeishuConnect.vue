<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>飞书机器人管理（per-机器人角色 = 登录账号权限级别）</span>
        <el-button type="primary" @click="onConnect" :loading="loading">🔗 扫码添加机器人</el-button>
      </div>
    </template>

    <el-table :data="robots" stripe size="small">
      <el-table-column prop="id" label="ID" width="50" />
      <el-table-column prop="name" label="名称" width="120" />
      <el-table-column prop="app_id" label="App ID" />
      <el-table-column label="角色" width="90">
        <template #default="{ row }">
          <el-tag :type="roleType(row.role)" size="small">{{ roleLabel(row.role) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="语言" width="80">
        <template #default="{ row }">{{ row.lang || '浏览器' }}</template>
      </el-table-column>
      <el-table-column prop="description" label="备注" />
      <el-table-column label="状态" width="70">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="310">
        <template #default="{ row }">
          <el-button size="small" @click="onTest(row.id)" :loading="testing === row.id">测试</el-button>
          <el-button v-if="!row.enabled" size="small" type="success" @click="onStart(row.id)">启动</el-button>
          <el-button v-else size="small" type="warning" @click="onStop(row.id)">停止</el-button>
          <el-button size="small" type="primary" @click="onSetting(row)">设置</el-button>
          <el-button size="small" type="danger" @click="onDelete(row.id)">删</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 扫码弹窗 -->
    <el-dialog v-model="scanVisible" title="扫码连接飞书机器人" width="360px">
      <div style="text-align: center">
        <div v-if="status === 'scanning'">
          <img v-if="qrImg" :src="qrImg" width="220" alt="二维码" />
          <p v-else>二维码生成中...</p>
          <p style="font-size: 13px; color: #999">有效期：{{ countdown }}s</p>
        </div>
        <el-result v-if="status === 'done'" icon="success" title="连接成功" sub-title="凭证已配置（默认 viewer 角色，可设置改）">
          <template #extra><el-button type="primary" @click="scanVisible = false">完成</el-button></template>
        </el-result>
        <el-alert v-if="status === 'error'" type="error" :title="errorMsg" show-icon :closable="false" />
      </div>
    </el-dialog>

    <!-- 设置弹窗 -->
    <el-dialog v-model="settingVisible" :title="`设置 - ${settingForm.name || ''}`" width="480px">
      <el-form :model="settingForm" label-width="90px">
        <el-form-item label="名称"><el-input v-model="settingForm.name" /></el-form-item>
        <el-form-item label="系统角色">
          <el-select v-model="settingForm.role" style="width: 100%">
            <el-option label="Viewer（只读：查持仓/盈亏/状态）" value="viewer" />
            <el-option label="Analyst（研究：策略/回测/数据）" value="analyst" />
            <el-option label="Trader（交易：启停策略/熔断/下单）" value="trader" />
            <el-option label="Admin（全权：+恢复/配置）" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="语言偏好">
          <el-select v-model="settingForm.lang" style="width: 100%">
            <el-option label="浏览器缺省（跟随用户）" value="" />
            <el-option label="中文" value="zh" />
            <el-option label="English" value="en" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="settingForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSaveSetting" :loading="savingSetting">确认</el-button>
          <el-button @click="settingVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getFeishuList, feishuConnect, feishuStatus, feishuStart, feishuStop, feishuDelete, feishuUpdate, testFeishu } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

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
    const lang = navigator.language || ''
    const { session_id } = await feishuConnect(lang)
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
          errorMsg.value = r.error || '连接失败'
        }
      } catch (e) { /* 忽略单次轮询失败 */ }
    }, 2000)
  } catch (e) {
    status.value = 'error'
    errorMsg.value = e.detail || '发起连接失败'
  }
  finally { loading.value = false }
}

const onSetting = (row) => {
  settingForm.value = { id: row.id, name: row.name || '', role: row.role || 'viewer', lang: row.lang || '', description: row.description || '' }
  settingVisible.value = true
}

const onSaveSetting = async () => {
  savingSetting.value = true
  try {
    await feishuUpdate(settingForm.value.id, settingForm.value)
    ElMessage.success('设置已更新（后续消息生效）')
    settingVisible.value = false
    load()
  } catch (e) { ElMessage.error(e.detail || '更新失败') }
  finally { savingSetting.value = false }
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testFeishu(id)
    if (r.ok) ElMessage.success('连接成功')
    else ElMessage.error('失败：' + r.error)
  } catch (e) { ElMessage.error('测试失败') }
  finally { testing.value = 0 }
}

const onStart = async (id) => {
  const r = await feishuStart(id)
  if (r.ok) { ElMessage.success('已启动'); load() }
  else ElMessage.error('失败：' + r.error)
}

const onStop = async (id) => {
  const r = await feishuStop(id)
  if (r.ok) { ElMessage.success('已停止'); load() }
  else ElMessage.error('失败：' + r.error)
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此机器人？', '提示', { type: 'warning' })
  await feishuDelete(id)
  ElMessage.success('已删除')
  load()
}
</script>
