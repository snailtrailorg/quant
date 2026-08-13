<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">总资产</div><div class="value">¥{{ (dashboard.total_value || 0).toFixed(0) }}</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">今日盈亏</div><div class="value" :style="{color: (dashboard.daily_pnl||0) >= 0 ? '#67c23a' : '#f56c6c'}">¥{{ (dashboard.daily_pnl || 0).toFixed(0) }}</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">总盈亏</div><div class="value" :style="{color: (dashboard.total_pnl||0) >= 0 ? '#67c23a' : '#f56c6c'}">¥{{ (dashboard.total_pnl || 0).toFixed(0) }} ({{ dashboard.total_pnl_pct || 0 }}%)</div></div></el-card>
      </el-col>
      <el-col :span="6">
        <el-card><div class="stat"><div class="label">回测/策略</div><div class="value">{{ dashboard.backtest_count ?? 0 }} / {{ strategies.length }}</div></div></el-card>
      </el-col>
    </el-row>
    <el-card style="margin-top: 20px">
      <template #header>策略运行状态</template>
      <el-table :data="strategies" stripe>
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="type" label="类型" />
        <el-table-column prop="symbol" label="标的" />
        <el-table-column label="状态">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '运行中' : '已停' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getStrategies, getDashboard } from '../api'
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
