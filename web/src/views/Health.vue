<template>
  <el-row :gutter="20">
    <el-col :span="12">
      <el-card>
        <template #header>{{ t('health.apiHealth') }}</template>
        <el-table :data="healthData" stripe>
          <el-table-column prop="name" :label="t('health.service')" width="120" />
          <el-table-column :label="t('common.status')" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" :label="t('common.detail')" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>{{ t('health.disk') }}</template>
        <el-table :data="diskData" stripe>
          <el-table-column prop="path" :label="t('health.path')" width="150" />
          <el-table-column prop="used" :label="t('health.used')" width="120" />
          <el-table-column prop="total" :label="t('health.total')" width="120" />
          <el-table-column :label="t('health.usage')" width="100">
            <template #default="{ row }">
              <el-progress :percentage="row.pct" :color="row.pct > 85 ? '#f56c6c' : '#67c23a'" :stroke-width="10" />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHealth } from '../api'

const { t } = useI18n()
const healthData = ref([])
const diskData = ref([])

onMounted(async () => {
  try {
    const r = await getHealth()
    if (r.results) {
      healthData.value = Object.entries(r.results).map(([k, v]) => ({
        name: k, status: v.status, detail: v.model || v.msg || '',
      }))
    }
    if (r.stats) {
      diskData.value = r.stats.filter(s => s.pct).map(s => ({
        path: s.path, used: `${s.used_gb}GB`, total: `${s.total_gb}GB`, pct: s.pct,
      }))
    }
  } catch { healthData.value = [{ name: '-', status: 'error', detail: t('common.loadFailed') }] }
})
</script>
