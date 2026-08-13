<template>
  <el-card>
    <template #header>审计日志</template>
    <el-form :inline="true" style="margin-bottom: 12px">
      <el-form-item label="操作人">
        <el-input v-model="filterActor" placeholder="筛选操作人" size="small" clearable style="width:140px" />
      </el-form-item>
      <el-form-item label="操作">
        <el-input v-model="filterAction" placeholder="登录/创建/删除..." size="small" clearable style="width:160px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" size="small" @click="load">筛选</el-button>
        <el-button size="small" @click="filterActor='';filterAction='';load()">重置</el-button>
      </el-form-item>
    </el-form>
    <el-table :data="filteredLogs" stripe>
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
import { ref, computed, onMounted } from 'vue'
import { getAudit } from '../api'
const logs = ref([])
const filterActor = ref('')
const filterAction = ref('')
const filteredLogs = computed(() => {
  let r = logs.value
  if (filterActor.value) r = r.filter(l => l.actor?.includes(filterActor.value))
  if (filterAction.value) r = r.filter(l => l.action?.includes(filterAction.value))
  return r
})
onMounted(async () => { try { logs.value = await getAudit() } catch (e) { console.error(e) } })
</script>