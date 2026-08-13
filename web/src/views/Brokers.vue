<template>
  <el-card>
    <template #header>交易通道管理（平台化交易层，券商/交易所接入配置）</template>
    <el-table :data="brokers" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="100" />
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
      <el-form-item label="Provider"><el-input v-model="form.provider" placeholder="xtp/binance/okx" /></el-form-item>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="凭证(JSON)">
        <el-input v-model="form.credentials" type="password" show-password placeholder='{"app_id":"...","app_secret":"..."} 编辑时留空不改' style="width:340px" />
      </el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? '更新' : '添加' }}</el-button>
        <el-button @click="resetForm">重置</el-button>
      </el-form-item>
    </el-form>
  </el-card>

  <!-- P2-4 通道用量监控 -->
  <el-card style="margin-top: 20px" v-loading="usageLoading">
    <template #header>通道调用量监控（P2-4）</template>
    <el-table :data="usage.today" stripe size="small">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="calls" label="今日调用" width="100" />
      <el-table-column prop="avg_latency_ms" label="平均延迟(ms)" width="120" />
      <el-table-column prop="success_rate" label="成功率%"><template #default="{ row }">{{ row.success_rate }}%</template></el-table-column>
    </el-table>
    <div v-if="!usage.today?.length" style="color:#999;font-size:12px;margin-top:8px">暂无用量数据（strategy_runner 下单后写入 broker_usage）</div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getBrokers, createBroker, updateBroker, deleteBroker, testBroker } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'

const brokers = ref([])
const form = ref(emptyForm())
const saving = ref(false)
const testing = ref(0)
const usage = ref({})
const usageLoading = ref(false)

const loadUsage = async () => { usageLoading.value = true; try { usage.value = await api.get('/broker-usage') } catch {} finally { usageLoading.value = false } }

function emptyForm() {
  return { provider: 'xtp', name: '', credentials: '', enabled: true }
}

const load = async () => { try { brokers.value = await getBrokers() } catch (e) { console.error(e) } }
onMounted(async () => { await load(); await loadUsage() })

const onEdit = (row) => { form.value = { ...row, credentials: '' } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateBroker(form.value.id, form.value)
    else await createBroker(form.value)
    ElMessage.success('保存成功')
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || '保存失败') }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此通道？', '提示', { type: 'warning' })
  await deleteBroker(id)
  ElMessage.success('已删除')
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testBroker(id)
    if (r.ok) ElMessage.success('凭证完整')
    else ElMessage.error('失败：' + r.error)
  } catch (e) { ElMessage.error('测试失败') }
  finally { testing.value = 0 }
}
</script>
