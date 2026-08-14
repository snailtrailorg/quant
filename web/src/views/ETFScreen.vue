<template>
  <el-card>
    <template #header>{{ t('screen.etfTitle') }}</template>
    <el-form :inline="true" :model="filters">
      <el-form-item :label="t('screen.limit')"><el-input-number v-model="filters.limit" :min="10" :max="500" size="small" /></el-form-item>
      <el-form-item><el-button type="primary" @click="screen" :loading="loading" size="small">{{ t('screen.filter') }}</el-button></el-form-item>
    </el-form>
    <el-table :data="results" stripe v-loading="loading" style="margin-top: 12px" @row-click="onRowClick">
      <el-table-column prop="ts_code" :label="t('screen.code')" width="100" />
      <el-table-column prop="name" :label="t('common.name')" width="200" />
      <el-table-column prop="management" :label="t('screen.management')" width="150" />
      <el-table-column prop="fund_type" :label="t('screen.fundType')" width="100" />
      <el-table-column :label="t('screen.kline')" width="60">
        <template #default="{ row }">
          <el-button size="small" @click.stop="onRowClick(row)">📊</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 12px; color:#999;font-size:12px">{{ t('screen.etfHint') }}</div>
    <KlineDialog v-model="klineVisible" :symbol="klineSymbol" :name="klineName" />
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
import KlineDialog from '../components/KlineDialog.vue'

const { t } = useI18n()
const loading = ref(false)
const results = ref([])
const filters = ref({ limit: 100 })
const klineVisible = ref(false)
const klineSymbol = ref('')
const klineName = ref('')

const screen = async () => {
  loading.value = true
  try {
    results.value = await api.get('/screen/etf', { params: filters.value })
    ElMessage.success(t('screen.screenSuccess', { n: results.value.length }))
  } catch { ElMessage.error(t('screen.screenFailed')) }
  finally { loading.value = false }
}
const onRowClick = (row) => {
  klineSymbol.value = row.ts_code
  klineName.value = row.name
  klineVisible.value = true
}
</script>
