<template>
  <el-card>
    <template #header>审计日志</template>
    <el-table :data="logs" stripe>
      <el-table-column prop="ts" label="时间" width="200">
        <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
      </el-table-column>
      <el-table-column prop="actor" label="操作人" width="120" />
      <el-table-column prop="action" label="操作" width="150" />
      <el-table-column prop="target" label="目标" width="150" />
      <el-table-column prop="detail" label="详情" />
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getAudit } from '../api'
const logs = ref([])
onMounted(async () => { logs.value = await getAudit() })
</script>
