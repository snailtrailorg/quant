<template>
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span>{{ t('task.title') }}</span>
        <!-- 批23：header 左标题/右动作组，统一序=筛选→页面特有（检测卡死）→刷新→列设置 -->
        <div style="display:flex;gap:8px;align-items:center">
          <RowFilter :model-value="filterStatus" :options="statusOptions" :title="t('common.filter')" @update:model-value="onFilter" />
          <IconBtn v-if="role==='admin'" type="warning" :icon="ZoomIn" :title="t('task.detectStuck')" @click="onDetectStuck" />
          <RefreshBtn @refresh="load" />
          <ColumnSettings storage-key="cols.tasks" :columns="taskColDefs" v-model:visible="taskVisible" />
        </div>
      </div>
    </template>
    <TableShell :data="tasks" fill :fill-reserve="60" storage-key="tasks">
      <el-table-column prop="id" :label="t('task.taskId')" min-width="120" show-overflow-tooltip />
      <el-table-column prop="name" :label="t('common.name')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('common.type')" min-width="100" />
      <el-table-column v-if="colOn('trigger_type')" prop="trigger_type" :label="t('cols.triggerType')" min-width="138">
        <template #default="{ row }">{{ triggerLabel(row.trigger_type) }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('trigger_user')" prop="trigger_user" :label="t('cols.triggeredBy')" min-width="138" show-overflow-tooltip />
      <el-table-column prop="status" :label="t('common.status')" min-width="100">
        <template #default="{ row }"><StatusTag :value="row.status" /></template>
      </el-table-column>
      <el-table-column prop="progress" :label="t('task.progress')" min-width="140">
        <template #default="{ row }">{{ row.progress?.pct || 0 }}% ({{ row.progress?.current || 0 }}/{{ row.progress?.total || 0 }})</template>
      </el-table-column>
      <el-table-column prop="last_heartbeat" :label="t('task.heartbeat')" min-width="160">
        <template #default="{ row }">{{ row.last_heartbeat ? fmtTime.full(row.last_heartbeat) : '-' }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('start_time')" prop="start_time" :label="t('cols.startTime')" min-width="220">
        <template #default="{ row }">{{ row.start_time ? fmtTime.full(row.start_time) : '-' }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('end_time')" prop="end_time" :label="t('cols.endTime')" min-width="220">
        <template #default="{ row }">{{ row.end_time ? fmtTime.full(row.end_time) : '-' }}</template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="130">
        <template #default="{ row }">
          <div style="display: inline-flex; gap: 6px; align-items: center; white-space: nowrap">
            <IconBtn size="small" :icon="View" :title="t('common.detail')" @click="onDetail(row.id)" />
            <IconBtn size="small" type="danger" :icon="SwitchButton" :title="t('task.terminate')" @click="onTerminate(row.id)" v-if="row.status==='running' && ['trader','admin'].includes(role)" />
            <IconBtn size="small" type="danger" :icon="Delete" :title="t('task.forceDelete')" @click="onForceDelete(row.id)" v-if="role==='admin'" />
          </div>
        </template>
      </el-table-column>
    </TableShell>

    <el-dialog v-model="detailVisible" :title="t('task.detailTitle')" width="720px">
      <div v-if="detail">
        <p>{{ t('common.name') }}: {{ detail.name }} | {{ t('common.type') }}: {{ detail.type }} | {{ t('common.status') }}: <StatusTag :value="detail.status" /></p>
        <p>{{ t('task.params') }}: {{ JSON.stringify(detail.params) }}</p>
        <p v-if="detail.error_message" style="color: var(--critical)">{{ t('task.error') }}: {{ detail.error_message }}</p>
        <el-divider />
        <h4 style="font-size: var(--fs-card); font-weight: 600">{{ t('task.execLogs') }}</h4>
        <TableShell :data="detail.logs" max-height="300" storage-key="task-logs">
          <el-table-column prop="level" :label="t('log.level')" min-width="80" />
          <el-table-column prop="message" :label="t('log.content')" show-overflow-tooltip />
          <el-table-column prop="step_name" :label="t('task.step')" min-width="120" />
          <el-table-column prop="created_at" :label="t('common.time')" min-width="220">
            <template #default="{ row }">{{ row.created_at ? fmtTime.full(row.created_at) : '' }}</template>
          </el-table-column>
        </TableShell>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import RowFilter from '../components/RowFilter.vue'
import RefreshBtn from '../components/RefreshBtn.vue'
import IconBtn from '../components/IconBtn.vue'
import { ZoomIn, SwitchButton, Delete, View } from '@element-plus/icons-vue'
import TableShell from '../components/TableShell.vue'
import { fmtTime } from '../utils/fmtTime'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTasks, getTaskDetail, terminateTask, forceDeleteTask, detectStuck } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const tasks = ref([])
// 批23：状态筛选多选（RowFilter 契约：[] =全部；空串单选退役）
const filterStatus = ref([])
const detailVisible = ref(false)
const detail = ref(null)
const role = ref(localStorage.getItem('role') || 'viewer')
const statusOptions = computed(() => [
  { value: 'running', label: t('task.statusRunning') },
  { value: 'completed', label: t('task.statusCompleted') },
  { value: 'failed', label: t('task.statusFailed') },
  { value: 'stuck', label: t('task.statusStuck') },
  { value: 'terminated', label: t('task.statusTerminated') },
])

// 批16 列显示配置（新列默认隐）：触发方式/触发人/起止时间——排查"谁在什么时候跑的"才开
const taskColDefs = computed(() => [
  { key: 'trigger_type', label: t('cols.triggerType'), hidden: true },
  { key: 'trigger_user', label: t('cols.triggeredBy'), hidden: true },
  { key: 'start_time', label: t('cols.startTime'), hidden: true },
  { key: 'end_time', label: t('cols.endTime'), hidden: true },
])
const taskVisible = ref([])
const colOn = k => taskVisible.value.includes(k)
// 盲审B-P2-7：触发方式枚举中文化（标签经文案师；未知值原样——排查新枚举不被吞）
const triggerLabel = v => v === 'manual' ? t('task.triggerManual') : v === 'schedule' ? t('task.triggerSchedule') : (v || '-')


// 后端 status=ANY(多值)（批23 A-P1-5）：逗号拼接重拉，空数组→无参=全部
const load = async () => { try { tasks.value = (await getTasks(filterStatus.value.join(','))).items || [] } catch (e) { console.error(e) } }
const onFilter = (v) => { filterStatus.value = v; load() }
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
