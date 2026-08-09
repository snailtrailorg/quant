<template>
  <el-card>
    <template #header>LLM 模型配置（DB 化，多模型不限种类）</template>
    <el-card shadow="never" style="margin-bottom: 12px">
      <template #header>📊 用量监控（本月）<el-button @click="loadUsage" size="small" link>刷新</el-button></template>
      <el-table :data="usage.month" stripe size="small">
        <el-table-column prop="provider" label="Provider" width="120" />
        <el-table-column prop="model" label="型号" />
        <el-table-column prop="calls" label="调用" width="80" />
        <el-table-column label="Token(入/出)" width="160">
          <template #default="{ row }">{{ row.input_tokens.toLocaleString() }} / {{ row.output_tokens.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="avg_latency_ms" label="延迟ms" width="80" />
        <el-table-column label="成功率" width="80">
          <template #default="{ row }"><el-tag :type="row.success_rate >= 95 ? 'success' : 'warning'" size="small">{{ row.success_rate }}%</el-tag></template>
        </el-table-column>
      </el-table>
      <div style="font-size: 12px; color: #999; margin-top: 8px">
        近7天：<span v-for="t in usage.trend" :key="t.date" style="margin-right: 10px">{{ t.date.slice(5) }} {{t.calls}}次/{{t.total_tokens.toLocaleString()}}tk</span><span v-if="!usage.trend.length">（暂无）</span>
      </div>
    </el-card>
    <el-table :data="models" stripe size="small">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="model" label="型号" />
      <el-table-column label="Key" width="80">
        <template #default="{ row }"><el-tag :type="row.has_key ? 'success' : 'info'" size="small">{{ row.has_key ? '已配' : '未配' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="80" />
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
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? '编辑模型' : '添加模型' }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="Provider"><el-input v-model="form.provider" placeholder="deepseek/glm/..." /></el-form-item>
      <el-form-item label="型号"><el-input v-model="form.model" /></el-form-item>
      <el-form-item label="API Key"><el-input v-model="form.api_key" type="password" show-password placeholder="编辑时留空不改" /></el-form-item>
      <el-form-item label="Base URL"><el-input v-model="form.base_url" /></el-form-item>
      <el-form-item label="最大输入tokens"><el-input-number v-model="form.max_input_tokens" :min="0" controls-position="right" placeholder="留空=不限" /></el-form-item>
      <el-form-item label="最大输出tokens"><el-input-number v-model="form.max_output_tokens" :min="0" controls-position="right" placeholder="留空=默认" /></el-form-item>
      <el-form-item label="优先级"><el-input-number v-model="form.priority" :min="1" :max="100" /></el-form-item>
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
import { getLLMModels, createLLMModel, updateLLMModel, deleteLLMModel, testLLMModel, getLLMUsage } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const models = ref([])
const usage = ref({ today: [], month: [], trend: [] })
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)

function emptyForm() {
  return { name: '', provider: '', model: '', api_key: '', base_url: '', priority: 10, enabled: false, context_window: 32768, supports_tools: true, max_input_tokens: null, max_output_tokens: null, temperature: null }
}

const load = async () => { models.value = await getLLMModels() }
const loadUsage = async () => { usage.value = await getLLMUsage() }
onMounted(() => { load(); loadUsage() })

const onEdit = (row) => { form.value = { ...row, api_key: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateLLMModel(form.value.id, form.value)
    else await createLLMModel(form.value)
    ElMessage.success('保存成功')
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || '保存失败') }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此模型？', '提示', { type: 'warning' })
  await deleteLLMModel(id)
  ElMessage.success('已删除')
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testLLMModel(id)
    if (r.ok) ElMessage.success('连接成功：' + (r.reply || ''))
    else ElMessage.error('失败：' + r.error)
  } catch (e) { ElMessage.error('测试失败') }
  finally { testing.value = 0 }
}
</script>
