<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>多策略实时监控看板</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
    <el-row :gutter="16" v-loading="loading">
      <el-col :span="12" v-for="s in strategies" :key="s.id" style="margin-bottom: 16px">
        <el-card shadow="hover">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ s.name }}</span>
              <el-tag :type="s.enabled ? 'success' : 'info'" size="small">
                {{ s.enabled ? '运行中' : '已停' }}
              </el-tag>
            </div>
          </template>
          <div style="margin-bottom: 12px">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="标的">{{ s.symbol }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ s.type }}</el-descriptions-item>
              <el-descriptions-item label="回测验证">{{ s.backtest_verified ? '✓' : '✗' }}</el-descriptions-item>
              <el-descriptions-item label="资产">¥{{ formatNum(s._equity) }}</el-descriptions-item>
            </el-descriptions>
          </div>
          <div style="height: 200px">
            <v-chart v-if="s._curve?.length" :option="chartOption(s)" autoresize style="height: 200px" />
            <div v-else style="text-align: center; color: #999; line-height: 200px">暂无净值数据（strategy_runner 写入后显示）</div>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <el-alert v-if="!strategies.length" type="info" :closable="false">暂无策略</el-alert>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getStrategies, getPnl } from '../api'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const strategies = ref([])
const loading = ref(false)
const formatNum = (n) => (n || 0).toFixed(0)
const chartOption = (s) => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 10, top: 10, bottom: 20 },
  xAxis: { type: 'category', data: (s._curve || []).map(c => c.ts?.slice(5, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ type: 'line', data: (s._curve || []).map(c => c.value), smooth: true, lineStyle: { width: 2 }, areaStyle: { opacity: 0.1 } }],
})

const load = async () => {
  loading.value = true
  try {
    strategies.value = await getStrategies()
    // P2-9：加载盈亏曲线（PnL 全局，分策略后可按 symbol 查）
    try {
      const pnl = await getPnl()
      const curve = pnl.curve || []
      const equity = pnl.total_value || 0
      strategies.value.forEach(s => { s._curve = curve; s._equity = equity })
    } catch { /* 无数据 */ }
  } finally { loading.value = false }
}
onMounted(load)
</script>
