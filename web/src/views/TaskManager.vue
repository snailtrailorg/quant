<template>
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span>{{ t('task.title') }}</span>
        <div style="display:flex;gap:8px;align-items:center">
          <el-select v-model="filterStatus" style="width:120px" @change="load">
            <el-option :label="t('common.all')" value="" />
            <el-option :label="t('task.statusRunning')" value="running" />
            <el-option :label="t('task.statusCompleted')" value="completed" />
            <el-option :label="t('task.statusFailed')" value="failed" />
            <el-option :label="t('task.statusStuck')" value="stuck" />
            <el-option :label="t('task.statusTerminated')" value="terminated" />
          </el-select>
          <el-button type="primary" @click="load">{{ t('common.refresh') }}</el-button>
          <el-button type="warning" @click="onDetectStuck" v-if="role==='admin'">{{ t('task.detectStuck') }}</el-button>
        </div>
      </div>
    </template>
    <el-table :data="tasks">
      <el-table-column prop="id" :label="t('task.taskId')" width="120" show-overflow-tooltip />
      <el-table-column prop="name" :label="t('common.name')" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('common.type')" width="80" />
      <el-table-column :label="t('common.status')" width="90">
        <template #default="{ row }"><StatusTag :value="row.status" /></template>
      </el-table-column>
      <el-table-column :label="t('task.progress')" width="140">
        <template #default="{ row }">{{ row.progress?.pct || 0 }}% ({{ row.progress?.current || 0 }}/{{ row.progress?.total || 0 }})</template>
      </el-table-column>
      <el-table-column prop="last_heartbeat" :label="t('task.heartbeat')" width="150">
        <template #default="{ row }">{{ row.last_heartbeat ? row.last_heartbeat.slice(0,19).replace('T',' ') : '-' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="300">
        <template #default="{ row }">
          <div style="display: inline-flex; gap: 6px; align-items: center; white-space: nowrap">
            <el-button type="primary" @click="onDetail(row.id)">{{ t('common.detail') }}</el-button>
            <el-button type="warning" @click="onTerminate(row.id)" v-if="row.status==='running' && ['trader','admin'].includes(role)">{{ t('task.terminate') }}</el-button>
            <el-button type="danger" @click="onForceDelete(row.id)" v-if="role==='admin'">{{ t('task.forceDelete') }}</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" :title="t('task.detailTitle')" width="720px">
      <div v-if="detail">
        <p>{{ t('common.name') }}: {{ detail.name }} | {{ t('common.type') }}: {{ detail.type }} | {{ t('common.status') }}: <StatusTag :value="detail.status" /></p>
        <p>{{ t('task.params') }}: {{ JSON.stringify(detail.params) }}</p>
        <p v-if="detail.error_message" style="color:#f56c6c">{{ t('task.error') }}: {{ detail.error_message }}</p>
        <el-divider />
        <h4>{{ t('task.execLogs') }}</h4>
        <el-table :data="detail.logs" max-height="300">
          <el-table-column prop="level" :label="t('log.level')" width="70" />
          <el-table-column prop="message" :label="t('log.content')" show-overflow-tooltip />
          <el-table-column prop="step_name" :label="t('task.step')" width="100" />
          <el-table-column prop="created_at" :label="t('common.time')" width="150">
            <template #default="{ row }">{{ row.created_at ? row.created_at.slice(0,19).replace('T',' ') : '' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTasks, getTaskDetail, terminateTask, forceDeleteTask, detectStuck } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const tasks = ref([])
const filterStatus = ref('')
const detailVisible = ref(false)
const detail = ref(null)
const role = ref(localStorage.getItem('role') || 'viewer')


const load = async () => { try { tasks.value = (await getTasks(filterStatus.value)).items || [] } catch (e) { console.error(e) } }
onMounted(load)

const onDetail = async (id) => {
  detail.value = await getTaskDetail(id)
  detailVisible.value = true
}
const onTerminate = async (id) => {
  await ElMessageBox.confirm(t('task.confirmTerminate'), t('common.tip'), { type: 'warning' })
  await terminateTask(id)
  ElMessage.success(t('task.terminated'))
  load()
}
const onForceDelete = async (id) => {
  await ElMessageBox.confirm(t('task.confirmForceDelete'), t('task.highRiskConfirm'), { type: 'warning' })
  await forceDeleteTask(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}
const onDetectStuck = async () => {
  const r = await detectStuck()
  ElMessage.success(t('task.markedStuck', { n: r.stuck_count }))
  load()
}
</script>
