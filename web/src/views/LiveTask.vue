<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>实盘任务</span>
        <el-button type="primary" size="small" @click="openCreate">创建实盘任务</el-button>
      </div>
    </template>
    <el-table :data="tasks" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="strategy_id" label="策略" width="150" />
      <el-table-column prop="symbol" label="标的" width="150" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="account_id" label="账户" width="150" />
      <el-table-column prop="initial_capital" label="资金" width="120" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button v-if="row.status !== 'running'" size="small" type="success" @click="onStart(row.id)">启动</el-button>
          <el-button v-if="row.status === 'running'" size="small" type="danger" @click="onStop(row.id)">停止</el-button>
          <el-button v-if="row.status !== 'running'" size="small" type="danger" link @click="onDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建实盘任务弹窗 -->
    <el-dialog v-model="dialogVisible" title="创建实盘任务" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="120px" v-loading="saving">
        <el-form-item label="任务名称">
          <el-input v-model="form.name" placeholder="如：茅台均线策略" />
        </el-form-item>
        <el-form-item label="策略">
          <el-select v-model="form.strategy_id" placeholder="选择策略" style="width: 100%" @change="onStrategyChange">
            <el-option v-for="s in strategies" :key="s.id" :label="`${s.name} (${s.id})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标的">
          <el-input v-model="form.symbol" placeholder="如 600000.SHSE" />
        </el-form-item>

        <el-divider content-position="left">任务参数</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: #999; font-size: 12px; padding-left: 120px">
          请先选择策略
        </div>

        <el-divider content-position="left">账户</el-divider>
        <el-form-item label="账户 ID">
          <el-input v-model="form.account_id" placeholder="账户 ID（可选）" />
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number v-model="form.initial_capital" :min="10000" :step="100000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save" :loading="saving">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getLiveTasks, createLiveTask, startLiveTask, stopLiveTask, deleteLiveTask, getStrategies } from '../api'
import ParameterForm from '../components/ParameterForm.vue'

const tasks = ref([])
const strategies = ref([])
const dialogVisible = ref(false)
const saving = ref(false)
const parameterDefs = ref([])
const form = ref({
  name: '', strategy_id: '', symbol: '', params: {},
  account_id: '', initial_capital: 1000000,
})

const statusType = (s) => ({
  running: 'success', pending: 'info', stopped: 'warning', error: 'danger'
}[s] || 'info')

const load = async () => {
  try { tasks.value = await getLiveTasks() } catch { ElMessage.error('加载失败') }
}
const loadStrategies = async () => {
  try { strategies.value = await getStrategies() } catch { strategies.value = [] }
}

const onStrategyChange = (sid) => {
  const s = strategies.value.find(x => x.id === sid)
  if (s?.params?.parameter_defs) {
    parameterDefs.value = s.params.parameter_defs
    form.value.params = {}
  } else {
    parameterDefs.value = []
    form.value.params = {}
  }
}

const openCreate = () => {
  form.value = { name: '', strategy_id: '', symbol: '', params: {}, account_id: '', initial_capital: 1000000 }
  parameterDefs.value = []
  dialogVisible.value = true
}

const save = async () => {
  if (!form.value.name || !form.value.strategy_id || !form.value.symbol) {
    ElMessage.warning('名称/策略/标的必填'); return
  }
  saving.value = true
  try {
    await createLiveTask(form.value)
    ElMessage.success('已创建')
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error('创建失败: ' + (e?.error || e?.message || '')) }
  finally { saving.value = false }
}

const onStart = async (id) => {
  try { await startLiveTask(id); ElMessage.success('已启动'); load() }
  catch (e) { ElMessage.error('启动失败') }
}
const onStop = async (id) => {
  try { await stopLiveTask(id); ElMessage.success('已停止'); load() }
  catch (e) { ElMessage.error('停止失败') }
}
const onDelete = async (id) => {
  try {
    await ElMessageBox.confirm('确认删除该实盘任务？', '确认')
    await deleteLiveTask(id); ElMessage.success('已删除'); load()
  } catch { /* 取消 */ }
}

onMounted(async () => { await load(); await loadStrategies() })
</script>