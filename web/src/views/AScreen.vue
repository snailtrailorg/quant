<template>
  <el-card>
    <template #header>A股筛选</template>
    <el-form :inline="true" :model="filters">
      <el-form-item label="PE ≤"><el-input-number v-model="filters.pe_max" :min="0" :step="5" size="small" /></el-form-item>
      <el-form-item label="PB ≤"><el-input-number v-model="filters.pb_max" :min="0" :step="0.5" :precision="2" size="small" /></el-form-item>
      <el-form-item label="市值≥(万)"><el-input-number v-model="filters.mv_min" :min="0" :step="1000000" size="small" /></el-form-item>
      <el-form-item label="换手率≥(%)"><el-input-number v-model="filters.turnover_min" :min="0" :step="0.5" :precision="2" size="small" /></el-form-item>
      <el-form-item label="数量"><el-input-number v-model="filters.limit" :min="10" :max="500" :step="50" size="small" /></el-form-item>
      <el-form-item><el-button type="primary" @click="screen" :loading="loading" size="small">筛选</el-button></el-form-item>
    </el-form>
    <el-table :data="results" stripe v-loading="loading" style="margin-top: 12px" @row-click="onRowClick">
      <el-table-column prop="ts_code" label="代码" width="100" />
      <el-table-column prop="name" label="名称" width="120" />
      <el-table-column prop="close" label="现价" width="80" />
      <el-table-column prop="pe" label="PE" width="80" sortable />
      <el-table-column prop="pe_ttm" label="PE(TTM)" width="90" sortable />
      <el-table-column prop="pb" label="PB" width="80" sortable />
      <el-table-column prop="turnover" label="换手率%" width="90" sortable />
      <el-table-column prop="total_mv" label="市值(万)" width="120" sortable>
        <template #default="{ row }">{{ row.total_mv ? (row.total_mv/10000).toFixed(0) : '-' }}</template>
      </el-table-column>
      <el-table-column label="K线" width="60">
        <template #default="{ row }">
          <el-button size="small" link type="primary" @click.stop="onRowClick(row)">📊</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 12px">
      <span style="color:#999;font-size:12px">点击行或📊按钮查看K线 · 数据来源：Tushare daily_basic</span>
    </div>
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
const filters = ref({ pe_max: 20, pb_max: 1.5, mv_min: 0, turnover_min: 0, limit: 100 })
const klineVisible = ref(false)
const klineSymbol = ref('')
const klineName = ref('')

const screen = async () => {
  loading.value = true
  try {
    results.value = await api.get('/screen/astock', { params: filters.value })
    ElMessage.success(`筛选到 ${results.value.length} 只`)
  } catch { ElMessage.error('筛选失败，请先同步基本面数据') }
  finally { loading.value = false }
}
const onRowClick = (row) => {
  klineSymbol.value = row.ts_code
  klineName.value = row.name
  klineVisible.value = true
}
</script>
