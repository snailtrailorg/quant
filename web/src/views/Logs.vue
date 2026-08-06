<template>
  <el-row :gutter="20">
    <el-col :span="14">
      <el-card>
        <template #header>运行日志</template>
        <el-table :data="logs" stripe height="500">
          <el-table-column prop="ts" label="时间" width="160">
            <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="level" label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="module" label="模块" width="100" />
          <el-table-column prop="msg" label="内容" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="10">
      <el-card>
        <template #header>告警历史</template>
        <el-table :data="alerts" stripe height="500">
          <el-table-column prop="level" label="级别" width="70">
            <template #default="{ row }">
              <el-tag :type="row.level === 'critical' ? 'danger' : row.level === 'warn' ? 'warning' : 'info'" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="标题" />
          <el-table-column prop="ts" label="时间" width="100">
            <template #default="{ row }">{{ (parseFloat(row.ts || 0) * 1000) ? new Date(parseFloat(row.ts) * 1000).toLocaleString() : '-' }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getLogs, getAlerts } from '../api'
const logs = ref([])
const alerts = ref([])
onMounted(async () => {
  try { logs.value = (await getLogs()).logs || [] } catch {}
  try { alerts.value = (await getAlerts()).alerts || [] } catch {}
})
</script>