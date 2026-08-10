<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>信号-委托-成交三账对账</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
    <el-alert :title="summary" :type="hasIssues ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />
    <el-table :data="issues" stripe>
      <el-table-column prop="type" label="异常类型" width="200" />
      <el-table-column prop="count" label="数量" width="80" />
      <el-table-column prop="detail" label="详情" />
    </el-table>
    <el-alert v-if="!issues.length" type="success" title="三账对账无异常" :closable="false" style="margin-top: 20px" />
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getReconcile } from '../api'

const issues = ref([])
const hasIssues = computed(() => issues.value.length > 0)
const summary = computed(() => hasIssues.value ? `发现 ${issues.value.length} 项异常` : '三账一致，无异常')

const load = async () => {
  try {
    const r = await getReconcile()
    issues.value = (r.issues || []).map(issue => ({ type: '异常', count: 1, detail: issue }))
  } catch (e) { ElMessage.error('对账查询失败') }
}
onMounted(load)
</script>