<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>数据完整性看板（{{ summary.total || 0 }} 只标的）</span>
        <el-radio-group v-model="freq" size="small" @change="load">
          <el-radio-button label="1D">日线</el-radio-button>
          <el-radio-button label="1min">1 分钟</el-radio-button>
          <el-radio-button label="5min">5 分钟</el-radio-button>
        </el-radio-group>
      </div>
    </template>

    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">完整</div><div style="font-size: 24px; color: #67c23a">{{ summary.complete || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">部分</div><div style="font-size: 24px; color: #e6a23c">{{ summary.partial || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">缺失</div><div style="font-size: 24px; color: #f56c6c">{{ summary.missing || 0 }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:#909399">完整率</div><div style="font-size: 24px">{{ completePct }}%</div></el-card></el-col>
    </el-row>

    <el-table :data="items" v-loading="loading" style="width: 100%" height="500">
      <el-table-column prop="symbol" label="标的" width="160" />
      <el-table-column prop="local_count" label="本地条数" width="100" />
      <el-table-column prop="first" label="首日" width="120" />
      <el-table-column prop="last" label="末日" width="120" />
      <el-table-column prop="expected" label="预期" width="100" />
      <el-table-column label="完整性%" width="180">
        <template #default="{ row }">
          <el-progress :percentage="Number(row.pct || 0)" :status="row.status === 'complete' ? 'success' : row.status === 'missing' ? 'exception' : ''" />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'complete' ? 'success' : row.status === 'partial' ? 'warning' : 'danger'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDataIntegrity } from '../api'

const items = ref([])
const summary = ref({})
const freq = ref('1D')
const loading = ref(false)

const completePct = computed(() => {
  const t = summary.value.total || 0
  return t ? Math.round((summary.value.complete || 0) / t * 100) : 0
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
