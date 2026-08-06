<template>
  <el-row :gutter="20">
    <el-col :span="12">
      <el-card>
        <template #header>接口健康</template>
        <el-table :data="healthData" stripe>
          <el-table-column prop="name" label="服务" width="120" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="详情" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>磁盘监控</template>
        <el-table :data="diskData" stripe>
          <el-table-column prop="path" label="路径" width="150" />
          <el-table-column prop="used" label="已用" width="120" />
          <el-table-column prop="total" label="总量" width="120" />
          <el-table-column label="使用率" width="100">
            <template #default="{ row }">
              <el-progress :percentage="row.pct" :color="row.pct > 85 ? '#f56c6c' : '#67c23a'" :stroke-width="10" />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getHealth } from '../api'

const healthData = ref([])
const diskData = ref([])

onMounted(async () => {
  try {
    const r = await getHealth()
    if (r.results) {
      healthData.value = Object.entries(r.results).map(([k, v]) => ({
        name: k, status: v.status, detail: v.model || v.msg || '',
      }))
    }
    if (r.stats) {
      diskData.value = r.stats.filter(s => s.pct).map(s => ({
        path: s.path, used: `${s.used_gb}GB`, total: `${s.total_gb}GB`, pct: s.pct,
      }))
    }
  } catch { healthData.value = [{ name: '-', status: 'error', detail: '加载失败' }] }
})
</script>