<template>
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span>后台任务管理（统一监控 + 卡死检测 + 强制删除）</span>
        <div style="display:flex;gap:8px;align-items:center">
          <el-select v-model="filterStatus" size="small" style="width:120px" @change="load">
            <el-option label="全部" value="" />
            <el-option label="运行中" value="running" />
            <el-option label="已完成" value="completed" />
            <el-option label="失败" value="failed" />
            <el-option label="卡死" value="stuck" />
            <el-option label="已终止" value="terminated" />
          </el-select>
          <el-button size="small" @click="load">刷新</el-button>
          <el-button size="small" type="warning" @click="onDetectStuck" v-if="role==='admin'">卡死检测</el-button>
        </div>
      </div>
    </template>
    <el-table :data="tasks" stripe size="small">
      <el-table-column prop="id" label="任务ID" width="120" show-overflow-tooltip />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="type" label="类型" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="进度" width="140">
        <template #default="{ row }">{{ row.progress?.pct || 0 }}% ({{ row.progress?.current || 0 }}/{{ row.progress?.total || 0 }})</template>
      </el-table-column>
      <el-table-column prop="last_heartbeat" label="心跳" width="150">
        <template #default="{ row }">{{ row.last_heartbeat ? row.last_heartbeat.slice(0,19).replace('T',' ') : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" @click="onDetail(row.id)">详情</el-button>
          <el-button size="small" type="warning" @click="onTerminate(row.id)" v-if="row.status==='running' && ['trader','admin'].includes(role)">终止</el-button>
          <el-button size="small" type="danger" @click="onForceDelete(row.id)" v-if="role==='admin'">强制删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="detailVisible" title="任务详情" width="700px">
      <div v-if="detail">
        <p>名称: {{ detail.name }} | 类型: {{ detail.type }} | 状态: <el-tag :type="statusType(detail.status)" size="small">{{ statusLabel(detail.status) }}</el-tag></p>
        <p>参数: {{ JSON.stringify(detail.params) }}</p>
        <p v-if="detail.error_message" style="color:#f56c6c">错误: {{ detail.error_message }}</p>
        <el-divider />
        <h4>执行日志（最近50条）</h4>
        <el-table :data="detail.logs" stripe size="small" max-height="300">
          <el-table-column prop="level" label="级别" width="70" />
          <el-table-column prop="message" label="内容" />
          <el-table-column prop="step_name" label="步骤" width="100" />
          <el-table-column prop="created_at" label="时间" width="150">
            <template #default="{ row }">{{ row.created_at ? row.created_at.slice(0,19).replace('T',' ') : '' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getTasks, getTaskDetail, terminateTask, forceDeleteTask, detectStuck } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const tasks = ref([])
const filterStatus = ref('')
const detailVisible = ref(false)
const detail = ref(null)
const role = ref(localStorage.getItem('role') || 'viewer')

const statusType = s => ({ running: 'warning', completed: 'success', failed: 'danger', stuck: 'danger', terminated: 'info', paused: 'info' }[s] || '')
const statusLabel = s => ({ running: '运行中', completed: '已完成', failed: '失败', stuck: '卡死', terminated: '已终止', paused: '已暂停' }[s] || s)

const load = async () => { try { tasks.value = (await getTasks(filterStatus.value)).items || [] } catch (e) { console.error(e) } }
onMounted(load)

const onDetail = async (id) => {
  detail.value = await getTaskDetail(id)
  detailVisible.value = true
}
const onTerminate = async (id) => {
  await ElMessageBox.confirm('确认终止此任务？', '提示', { type: 'warning' })
  await terminateTask(id)
  ElMessage.success('已终止')
  load()
}
const onForceDelete = async (id) => {
  await ElMessageBox.confirm('确认强制删除此任务（卡死清理）？', '高危确认', { type: 'warning' })
  await forceDeleteTask(id)
  ElMessage.success('已删除')
  load()
}
const onDetectStuck = async () => {
  const r = await detectStuck()
  ElMessage.success(`标记 ${r.stuck_count} 个卡死任务`)
  load()
}
</script>
