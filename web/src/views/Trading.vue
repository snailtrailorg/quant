<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>实盘交易看板</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总资产</div><div class="value">¥{{ formatNum(pnlData.total_value) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">今日盈亏</div><div class="value" :style="{color: pnlData.today_pnl >= 0 ? '#67c23a' : '#f56c6c'}">¥{{ formatNum(pnlData.today_pnl) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总盈亏</div><div class="value" :style="{color: (pnlData.total_pnl||0) >= 0 ? '#67c23a' : '#f56c6c'}">¥{{ formatNum(pnlData.total_pnl) }} ({{ pnlData.total_pnl_pct || 0 }}%)</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">持仓数</div><div class="value">{{ positionData.positions?.length || 0 }}</div></div></el-card></el-col>
    </el-row>
    <el-tabs>
      <el-tab-pane label="持仓">
        <el-table :data="positionData.positions || []" stripe>
          <el-table-column prop="symbol" label="标的" width="120" />
          <el-table-column prop="volume" label="数量" width="80" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="订单">
        <el-table :data="ordersData.orders || []" stripe>
          <el-table-column prop="ts" label="时间" width="160" />
          <el-table-column prop="symbol" label="标的" width="120" />
          <el-table-column prop="action" label="方向" width="80" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="price" label="价格" width="100" />
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="盈亏曲线">
        <div v-if="pnlData.curve?.length" style="height: 400px">
          <v-chart :option="pnlChartOption" autoresize />
        </div>
        <div v-else style="height: 400px; display: flex; align-items: center; justify-content: center; color: #999">
          暂无盈亏数据（strategy_runner 写 account_snapshot 后显示）
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getPosition, getOrders, getPnl } from '../api'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const positionData = ref({})
const ordersData = ref({})
const pnlData = ref({})
const pnlChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: (pnlData.value.curve || []).map(c => c.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ name: '净值', type: 'line', data: (pnlData.value.curve || []).map(c => c.value), smooth: true }],
}))
const formatNum = (n) => (n || 0).toFixed(0)
const load = async () => {
  try { positionData.value = await getPosition() } catch {}
  try { ordersData.value = await getOrders() } catch {}
  try { pnlData.value = await getPnl() } catch {}
}
onMounted(load)
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>
