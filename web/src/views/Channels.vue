<template>
  <el-card>
    <template #header>消息通道管理（平台化消息层，统一告警/AI 输出渠道）</template>
    <el-table :data="channels" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="name" label="名称" />
      <el-table-column label="凭证" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'" size="small">{{ row.has_credentials ? '已配' : '未配' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="启用" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="onTest(row.id)" :loading="testing === row.id">测试</el-button>
          <el-button size="small" type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? '编辑通道' : '添加通道' }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="Provider"><el-input v-model="form.provider" placeholder="wechat_work/discord/serverchan" /></el-form-item>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="凭证(webhook/key)"><el-input v-model="form.credentials" type="password" show-password placeholder="编辑时留空不改" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? '更新' : '添加' }}</el-button>
        <el-button @click="resetForm">重置</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getChannels, createChannel, updateChannel, deleteChannel, testChannel } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const channels = ref([])
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { provider: 'wechat_work', name: '', credentials: '', enabled: true }
}

const load = async () => { channels.value = await getChannels() }
onMounted(load)

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateChannel(form.value.id, form.value)
    else await createChannel(form.value)
    ElMessage.success('保存成功')
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || '保存失败') }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此通道？', '提示', { type: 'warning' })
  await deleteChannel(id)
  ElMessage.success('已删除')
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testChannel(id)
    if (r.ok) ElMessage.success('发送成功')
    else ElMessage.error('失败：' + r.error)
  } catch (e) { ElMessage.error('测试失败') }
  finally { testing.value = 0 }
}
</script>
