<template>
  <el-card>
    <template #header>{{ t('screen.cbTitle') }}</template>
    <el-form :inline="true" :model="filters">
      <el-form-item :label="t('screen.limit')"><el-input-number v-model="filters.limit" :min="10" :max="500" size="small" /></el-form-item>
      <el-form-item><el-button type="primary" @click="screen" :loading="loading" size="small">{{ t('screen.filter') }}</el-button></el-form-item>
    </el-form>
    <el-table :data="results" stripe v-loading="loading" style="margin-top: 12px" @row-click="onRowClick">
      <el-table-column prop="ts_code" :label="t('screen.code')" width="100" />
      <el-table-column prop="name" :label="t('common.name')" width="100" />
      <el-table-column prop="stk_code" :label="t('screen.stkCode')" width="100" />
      <el-table-column prop="stk_name" :label="t('screen.stkName')" width="100" />
      <el-table-column prop="conv_price" :label="t('screen.convPrice')" width="80" />
      <el-table-column prop="maturity_date" :label="t('screen.maturityDate')" width="100" />
      <el-table-column :label="t('screen.kline')" width="60">
        <template #default="{ row }">
          <el-button size="small" @click.stop="onRowClick(row)">📊</el-button>
        </template>
      </el-table-column>
      <el-table-column :label="t('screen.aiTerms')" width="100">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click.stop="showTerms(row)">{{ t('screen.termsBtn') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 12px; color:#999;font-size:12px">{{ t('screen.cbHint') }}</div>
    <KlineDialog v-model="klineVisible" :symbol="klineSymbol" :name="klineName" />
    <el-dialog v-model="termsVisible" :title="t('screen.aiTermsTitle', { symbol: termsSymbol })" width="600px">
      <el-input v-model="termsResult" type="textarea" :rows="10" readonly v-loading="termsLoading" />
      <template #footer><el-button size="small" @click="termsVisible = false">{{ t('common.close') }}</el-button></template>
    </el-dialog>
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
const termsVisible = ref(false)
const termsSymbol = ref('')
const termsResult = ref('')
const termsLoading = ref(false)

const screen = async () => {
  loading.value = true
  try {
    results.value = await api.get('/screen/cb', { params: filters.value })
    ElMessage.success(t('screen.screenSuccess', { n: results.value.length }))
  } catch { ElMessage.error(t('screen.screenFailed')) }
  finally { loading.value = false }
}
const onRowClick = (row) => {
  klineSymbol.value = row.ts_code
  klineName.value = row.name
  klineVisible.value = true
}
const showTerms = async (row) => {
  termsSymbol.value = row.ts_code
  termsVisible.value = true
  termsLoading.value = true
  termsResult.value = ''
  try {
    const r = await api.get('/convertible/terms', { params: { ts_code: row.ts_code } })
    termsResult.value = r.summary || t('screen.noTerms')
  } catch (e) { termsResult.value = t('screen.termsFailed') }
  finally { termsLoading.value = false }
}
</script>
