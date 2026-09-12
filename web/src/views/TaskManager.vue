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
          <ColumnSettings storage-key="cols.tasks" :columns="taskColDefs" v-model:visible="taskVisible" />
          <el-button type="primary" @click="load">{{ t('common.refresh') }}</el-button>
          <el-button type="warning" @click="onDetectStuck" v-if="role==='admin'">{{ t('task.detectStuck') }}</el-button>
        </div>
      </div>
    </template>
    <el-table :data="tasks">
      <el-table-column prop="id" :label="t('task.taskId')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="name" :label="t('common.name')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('common.type')" min-width="100" />
      <el-table-column v-if="colOn('trigger_type')" prop="trigger_type" :label="t('cols.triggerType')" min-width="110" />
      <el-table-column v-if="colOn('trigger_user')" prop="trigger_user" :label="t('cols.triggeredBy')" min-width="110" show-overflow-tooltip />
      <el-table-column :label="t('common.status')" min-width="100">
        <template #default="{ row }"><StatusTag :value="row.status" /></template>
      </el-table-column>
      <el-table-column :label="t('task.progress')" min-width="140">
        <template #default="{ row }">{{ row.progress?.pct || 0 }}% ({{ row.progress?.current || 0 }}/{{ row.progress?.total || 0 }})</template>
      </el-table-column>
      <el-table-column prop="last_heartbeat" :label="t('task.heartbeat')" min-width="160">
        <template #default="{ row }">{{ row.last_heartbeat ? fmtTime.full(row.last_heartbeat) : '-' }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('start_time')" prop="start_time" :label="t('cols.startTime')" min-width="160">
        <template #default="{ row }">{{ row.start_time ? fmtTime.full(row.start_time) : '-' }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('end_time')" prop="end_time" :label="t('cols.endTime')" min-width="160">
        <template #default="{ row }">{{ row.end_time ? fmtTime.full(row.end_time) : '-' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="250">
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
        <p v-if="detail.error_message" style="color: var(--critical)">{{ t('task.error') }}: {{ detail.error_message }}</p>
        <el-divider />
        <h4>{{ t('task.execLogs') }}</h4>
        <el-table :data="detail.logs" max-height="300">
          <el-table-column prop="level" :label="t('log.level')" min-width="80" />
          <el-table-column prop="message" :label="t('log.content')" show-overflow-tooltip />
          <el-table-column prop="step_name" :label="t('task.step')" min-width="120" />
          <el-table-column prop="created_at" :label="t('common.time')" min-width="160">
            <template #default="{ row }">{{ row.created_at ? fmtTime.full(row.created_at) : '' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import { fmtTime } from '../utils/fmtTime'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTasks, getTaskDetail, terminateTask, forceDeleteTask, detectStuck } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const tasks = ref([])
const filterStatus = ref('')
const detailVisible = ref(false)
const detail = ref(null)
const role = ref(localStorage.getItem('role') || 'viewer')

// 批16 列显示配置（新列默认隐）：触发方式/触发人/起止时间——排查"谁在什么时候跑的"才开
const taskColDefs = computed(() => [
  { key: 'trigger_type', label: t('cols.triggerType'), hidden: true },
  { key: 'trigger_user', label: t('cols.triggeredBy'), hidden: true },
  { key: 'start_time', label: t('cols.startTime'), hidden: true },
  { key: 'end_time', label: t('cols.endTime'), hidden: true },
])
const taskVisible = ref([])
const colOn = k => taskVisible.value.includes(k)


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
