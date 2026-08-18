<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('liveTask.title') }}</span>
        <el-button type="primary" @click="openCreate">{{ t('liveTask.create') }}</el-button>
      </div>
    </template>
    <el-table :data="tasks" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" :label="t('common.name')" />
      <el-table-column prop="strategy_id" :label="t('liveTask.strategy')" width="150" />
      <el-table-column prop="symbol" :label="t('common.symbol')" width="150" />
      <el-table-column :label="t('common.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="account_id" :label="t('common.account')" width="150" />
      <el-table-column prop="initial_capital" :label="t('liveTask.capital')" width="120" />
      <el-table-column :label="t('common.action')" width="250">
        <template #default="{ row }">
          <el-button v-if="row.status !== 'running'" type="success" @click="onStart(row.id)">{{ t('common.start') }}</el-button>
          <el-button v-if="row.status === 'running'" type="danger" @click="onStop(row.id)">{{ t('common.stop') }}</el-button>
          <el-button v-if="row.status !== 'running'" type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建实盘任务弹窗 -->
    <el-dialog v-model="dialogVisible" :title="t('liveTask.create')" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="120px" v-loading="saving">
        <el-form-item :label="t('liveTask.taskName')">
          <el-input v-model="form.name" :placeholder="t('liveTask.phName')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.strategy')">
          <el-select v-model="form.strategy_id" :placeholder="t('liveTask.phStrategy')" style="width: 100%" @change="onStrategyChange">
            <el-option v-for="s in strategies" :key="s.id" :label="`${s.name} (${s.id})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.symbol')">
          <el-input v-model="form.symbol" :placeholder="t('liveTask.phSymbol')" />
        </el-form-item>

        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: #999; font-size: 12px; padding-left: 120px">
          {{ t('liveTask.selectStrategyFirst') }}
        </div>

        <el-divider content-position="left">{{ t('common.account') }}</el-divider>
        <el-form-item :label="t('liveTask.accountId')">
          <el-input v-model="form.account_id" :placeholder="t('liveTask.phAccountId')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="form.initial_capital" :min="10000" :step="100000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving">{{ t('liveTask.createBtn') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getLiveTasks, createLiveTask, startLiveTask, stopLiveTask, deleteLiveTask, getStrategies , apiErr } from '../api'
import ParameterForm from '../components/ParameterForm.vue'

const { t } = useI18n()
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
  try { tasks.value = await getLiveTasks() } catch { ElMessage.error(t('common.loadFailed')) }
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
    ElMessage.warning(t('liveTask.requiredHint')); return
  }
  saving.value = true
  try {
    await createLiveTask(form.value)
    ElMessage.success(t('common.createSuccess'))
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.createFailed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

const onStart = async (id) => {
  try { await startLiveTask(id); ElMessage.success(t('common.started')); load() }
  catch (e) { ElMessage.error(t('common.startFailed')) }
}
const onStop = async (id) => {
  try { await stopLiveTask(id); ElMessage.success(t('common.stopped')); load() }
  catch (e) { ElMessage.error(t('common.stopFailed')) }
}
const onDelete = async (id) => {
  try {
    await ElMessageBox.confirm(t('liveTask.confirmDelete'), t('common.confirm'))
    await deleteLiveTask(id); ElMessage.success(t('common.deleteSuccess')); load()
  } catch { /* 取消 */ }
}

onMounted(async () => { await load(); await loadStrategies() })
</script>
