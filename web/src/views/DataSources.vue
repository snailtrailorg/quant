<template>
  <el-card>
    <template #header>数据源管理（平台化数据层，配置 DB 化）</template>
    <el-card v-if="usage.today && usage.today.length" shadow="never" style="margin-bottom: 12px">
      <div style="font-weight: bold; margin-bottom: 8px">数据源调用量（今日，A4 #36）</div>
      <el-table :data="usage.today" size="small">
        <el-table-column prop="provider" label="Provider" width="120" />
        <el-table-column prop="calls" label="调用数" width="100" />
        <el-table-column prop="records" label="记录数" width="100" />
        <el-table-column label="失败" width="80">
          <template #default="{ row }"><el-tag :type="row.failures > 0 ? 'danger' : 'success'" size="small">{{ row.failures }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="avg_latency" label="均延迟(ms)" width="110" />
      </el-table>
    </el-card>
    <el-table :data="sources" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="name" label="名称" />
      <el-table-column label="凭证" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'" size="small">{{ row.has_credentials ? '已配' : '未配' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="usage_limit" label="日限额" width="80" />
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
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? '编辑数据源' : '添加数据源' }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="Provider"><el-input v-model="form.provider" placeholder="tushare/wind/akshare" /></el-form-item>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="凭证(Token)"><el-input v-model="form.credentials" type="password" show-password placeholder="编辑时留空不改" /></el-form-item>
      <el-form-item label="日限额"><el-input-number v-model="form.usage_limit" :min="0" controls-position="right" /></el-form-item>
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
import { getDataSources, createDataSource, updateDataSource, deleteDataSource, testDataSource, getDataSourceUsage } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const sources = ref([])
const usage = ref({ today: [], trend: [] })
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { provider: 'tushare', name: '', credentials: '', usage_limit: null, enabled: true }
}

const load = async () => { try { sources.value = await getDataSources() } catch (e) { console.error(e) } }
const loadUsage = async () => { try { usage.value = await getDataSourceUsage() } catch (e) { console.error(e) } }
onMounted(() => { load(); loadUsage() })

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateDataSource(form.value.id, form.value)
    else await createDataSource(form.value)
    ElMessage.success('保存成功')
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || '保存失败') }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此数据源？', '提示', { type: 'warning' })
  await deleteDataSource(id)
  ElMessage.success('已删除')
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testDataSource(id)
    if (r.ok) ElMessage.success('连接成功')
    else ElMessage.error('失败：' + r.error)
  } catch (e) { ElMessage.error('测试失败') }
  finally { testing.value = 0 }
}
</script>
