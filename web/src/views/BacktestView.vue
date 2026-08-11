<template>
  <el-card v-loading="loading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>回测可视化 - {{ symbol }}</span>
        <el-button @click="$router.back()">返回</el-button>
      </div>
    </template>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总收益</div><div class="value">{{ result.total_return_pct }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">胜率</div><div class="value">{{ result.win_rate }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">夏普</div><div class="value">{{ result.sharpe_ratio }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">最大回撤</div><div class="value">{{ result.max_drawdown_pct }}%</div></div></el-card></el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="净值曲线" name="equity">
        <v-chart :option="equityOption" autoresize style="height: 400px" />
      </el-tab-pane>
      <el-tab-pane label="回撤曲线（P3-3）" name="drawdown">
        <v-chart :option="drawdownOption" autoresize style="height: 400px" v-if="drawdownData.length" />
        <div v-else style="height:400px;display:flex;align-items:center;justify-content:center;color:#999">暂无回撤数据</div>
      </el-tab-pane>
      <el-tab-pane label="交易明细" name="trades">
        <el-table :data="trades" stripe max-height="400">
          <el-table-column prop="ts" label="时间" width="180" />
          <el-table-column prop="action" label="方向" width="80" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="price" label="价格" width="100" />
          <el-table-column prop="commission" label="佣金" width="100" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getBacktestRun } from '../api'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const route = useRoute()
const runId = route.params.id
const symbol = route.params.symbol
const loading = ref(false)
const result = ref({})
const trades = ref([])
const dailyValues = ref([])
const drawdownData = ref([])
const activeTab = ref('equity')
let eventSource = null

const equityOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['净值'] },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: dailyValues.value.map(d => d.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{
    name: '净值', type: 'line', data: dailyValues.value.map(d => d.value),
    smooth: true, lineStyle: { width: 2 }, areaStyle: { opacity: 0.1 },
  }],
}))

const drawdownOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: drawdownData.value.map(d => d.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ name: '回撤', type: 'line', data: drawdownData.value.map(d => d.dd), smooth: true, lineStyle: { width: 2, color: '#f56c6c' }, areaStyle: { opacity: 0.1, color: '#f56c6c' } }],
}))

onMounted(async () => {
  loading.value = true
  try {
    const data = await getBacktestRun(runId)
    const symData = data.symbols?.find(s => s.symbol === symbol) || {}
    result.value = symData.result || {}
    trades.value = result.value.trades || []
    dailyValues.value = result.value.daily_values || []
    // 计算回撤曲线
    let peak = 0
    drawdownData.value = (result.value.daily_values || []).map(d => {
      const v = d.value
      if (v > peak) peak = v
      return { ts: d.ts, dd: peak > 0 ? ((peak - v) / peak * 100) : 0 }
    })

    // P2-12: SSE 实时流（回测运行时）
    const token = localStorage.getItem('token')
    if (token && data.status === 'running') {
      eventSource = new EventSource(`/api/backtest/${runId}/${symbol}/stream?token=${token}`)
      eventSource.onmessage = (e) => {
        const frame = JSON.parse(e.data)
        if (frame.error) { eventSource.close(); return }
        if (frame.daily_values) { dailyValues.value = frame.daily_values }
        if (frame.trades) { trades.value = frame.trades }
        if (frame.total_return_pct !== undefined) {
          result.value = frame
          eventSource.close()
        }
      }
    }
  } catch (e) { ElMessage.error('加载可视化数据失败') }
  finally { loading.value = false }
})

onUnmounted(() => { if (eventSource) eventSource.close() })
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>