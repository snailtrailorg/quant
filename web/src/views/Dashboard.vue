<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">{{ t('trading.totalAssets') }}</div><div class="value">¥{{ (dashboard.total_value || 0).toFixed(0) }}</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">{{ t('trading.todayPnl') }}</div><div class="value" :style="{color: (dashboard.daily_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (dashboard.daily_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ (dashboard.daily_pnl || 0).toFixed(0) }}</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">{{ t('trading.totalPnl') }}</div><div class="value" :style="{color: (dashboard.total_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (dashboard.total_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ (dashboard.total_pnl || 0).toFixed(0) }} ({{ dashboard.total_pnl_pct || 0 }}%)"</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">{{ t('dashboard.backtestStrategy') }}</div><div class="value">{{ dashboard.backtest_count ?? 0 }} / {{ strategies.length }}</div></div></el-card>
      </el-col>
    </el-row>
    <el-card style="margin-top: 20px">
      <template #header>{{ t('dashboard.strategyStatus') }}</template>
      <el-table :data="strategies" stripe>
        <el-table-column prop="name" :label="t('common.name')" />
        <el-table-column prop="type" :label="t('common.type')" />
        <el-table-column prop="symbol" :label="t('common.symbol')" />
        <el-table-column :label="t('common.status')">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? t('strategy.statusRunning') : t('strategy.statusStopped') }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getStrategies, getDashboard } from '../api'
const { t } = useI18n()
const strategies = ref([])
const dashboard = ref({})
onMounted(async () => {
  try { strategies.value = await getStrategies() } catch (e) { console.error(e) }
  try { dashboard.value = await getDashboard() } catch (e) {}
})
</script>

<style scoped>
.stat { text-align: center; padding: 20px 0; }
.stat .label { color: #909399; font-size: 14px; }
.stat .value { font-size: 28px; font-weight: bold; color: #303133; margin-top: 8px; }
</style>
