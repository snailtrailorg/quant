<template>
  <el-card>
    <template #header>可转债筛选</template>
    <el-form :inline="true" :model="filters">
      <el-form-item label="数量"><el-input-number v-model="filters.limit" :min="10" :max="500" size="small" /></el-form-item>
      <el-form-item><el-button type="primary" @click="screen" :loading="loading" size="small">筛选</el-button></el-form-item>
    </el-form>
    <el-table :data="results" stripe v-loading="loading" style="margin-top: 12px" @row-click="onRowClick">
      <el-table-column prop="ts_code" label="代码" width="100" />
      <el-table-column prop="name" label="名称" width="100" />
      <el-table-column prop="stk_code" label="正股代码" width="100" />
      <el-table-column prop="stk_name" label="正股名称" width="100" />
      <el-table-column prop="conv_price" label="转股价" width="80" />
      <el-table-column prop="maturity_date" label="到期日" width="100" />
      <el-table-column label="K线" width="60">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click.stop="onRowClick(row)">📊</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 12px; color:#999;font-size:12px">点击行查看K线 · 数据来源：Tushare cb_daily + cb_basic</div>
    <KlineDialog v-model="klineVisible" :symbol="klineSymbol" :name="klineName" />
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import KlineDialog from '../components/KlineDialog.vue'

const loading = ref(false)
const results = ref([])
const filters = ref({ limit: 100 })
const klineVisible = ref(false)
const klineSymbol = ref('')
const klineName = ref('')

const screen = async () => {
  loading.value = true
  try {
    results.value = await api.get('/screen/cb', { params: filters.value })
    ElMessage.success(`筛选到 ${results.value.length} 只`)
  } catch { ElMessage.error('筛选失败') }
  finally { loading.value = false }
}
const onRowClick = (row) => {
  klineSymbol.value = row.ts_code
  klineName.value = row.name
  klineVisible.value = true
}
</script>
