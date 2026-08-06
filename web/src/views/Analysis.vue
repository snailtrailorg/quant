<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('analysis.title') }}</span>
        <el-button @click="load" :loading="loading">{{ t('analysis.refresh') }}</el-button>
      </div>
    </template>
    <el-table :data="results" stripe>
      <el-table-column prop="symbol" label="股票" width="120" />
      <el-table-column prop="score" :label="t('analysis.score')" width="100" sortable />
      <el-table-column :label="t('analysis.rating')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.rating === 'BUY' ? 'success' : row.rating === 'AVOID' ? 'danger' : 'info'">{{ row.rating }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="support" :label="t('analysis.support')" width="100" />
      <el-table-column prop="resistance" :label="t('analysis.resistance')" width="100" />
      <el-table-column prop="conclusion" :label="t('analysis.conclusion')" />
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getAstockSelection } from '../api'

const { t } = useI18n()
const results = ref([])
const loading = ref(false)
const load = async () => {
  loading.value = true
  try { results.value = await getAstockSelection('') } finally { loading.value = false }
}
onMounted(load)
</script>
