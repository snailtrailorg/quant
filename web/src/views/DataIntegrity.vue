<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('dataIntegrity.title', { n: summary.total || 0 }) }}</span>
        <el-radio-group v-model="freq" @change="load">
          <el-radio-button value="1D">{{ t('dataIntegrity.daily') }}</el-radio-button>
          <el-radio-button value="1min">{{ t('dataIntegrity.min1') }}</el-radio-button>
          <el-radio-button value="5min">{{ t('dataIntegrity.min5') }}</el-radio-button>
        </el-radio-group>
      </div>
    </template>

    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">{{ t('dataIntegrity.complete') }}</div><div style="font-size: 24px; color: #67c23a">{{ summary.complete || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">{{ t('dataIntegrity.partial') }}</div><div style="font-size: 24px; color: #e6a23c">{{ summary.partial || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">{{ t('dataIntegrity.missing') }}</div><div style="font-size: 24px; color: #f56c6c">{{ summary.missing || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">{{ t('dataIntegrity.completeRate') }}</div><div style="font-size: 24px">{{ completePct }}%</div></el-card></el-col>
    </el-row>

    <el-table :data="items" v-loading="loading" style="width: 100%" height="500">
      <el-table-column prop="symbol" :label="t('common.symbol')" width="160" />
      <el-table-column prop="local_count" :label="t('dataIntegrity.localCount')" width="100" />
      <el-table-column prop="first" :label="t('dataIntegrity.first')" width="120" />
      <el-table-column prop="last" :label="t('dataIntegrity.last')" width="120" />
      <el-table-column prop="expected" :label="t('dataIntegrity.expected')" width="100" />
      <el-table-column :label="t('dataIntegrity.integrityPct')" width="180">
        <template #default="{ row }">
          <el-progress :percentage="Number(row.pct || 0)" :status="row.status === 'complete' ? 'success' : row.status === 'missing' ? 'exception' : ''" />
        </template>
      </el-table-column>
      <el-table-column :label="t('common.status')" width="90">
        <template #default="{ row }">
          <StatusTag :value="row.status" />
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getDataIntegrity } from '../api'

const { t } = useI18n()
const items = ref([])
const summary = ref({})
const freq = ref('1D')
const loading = ref(false)

const completePct = computed(() => {
  const total = summary.value.total || 0
  return total ? Math.round((summary.value.complete || 0) / total * 100) : 0
})

const load = async () => {
  loading.value = true
  try {
    const r = await getDataIntegrity(freq.value)
    items.value = r.items || []
    summary.value = r.summary || {}
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
