<template>
  <el-card v-loading="loading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('backtest.viewTitle', { symbol }) }}</span>
        <div>
          <el-button @click="exportReport">{{ t('backtest.exportReport') }}</el-button>
          <el-button type="primary" @click="$router.back()">{{ t('common.return') }}</el-button>
        </div>
      </div>
    </template>

    <!-- 指标概览（ptrade 批 3：10 指标） -->
    <el-row :gutter="12" style="margin-bottom: 20px">
      <el-col :span="4" v-for="m in overviewMetrics" :key="m.key" style="margin-bottom: 12px">
        <el-card shadow="hover"><div class="stat">
          <div class="label">{{ m.label }}</div>
          <div class="value">{{ m.value }}</div>
        </div></el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="t('backtest.equityCurve')" name="equity">
        <v-chart :option="equityOption" autoresize style="height: 400px" />
      </el-tab-pane>
      <el-tab-pane :label="t('backtest.drawdownCurve')" name="drawdown">
        <v-chart :option="drawdownOption" autoresize style="height: 400px" v-if="drawdownData.length" />
        <div v-else style="height:400px;display:flex;align-items:center;justify-content:center;color:var(--text-secondary)">{{ t('backtest.noDrawdown') }}</div>
      </el-tab-pane>
      <el-tab-pane :label="t('backtest.trades')" name="trades">
        <el-table :data="trades" max-height="400">
          <el-table-column prop="ts" :label="t('trading.time')" width="180" />
          <el-table-column prop="action" :label="t('trading.direction')" width="80" />
          <el-table-column prop="volume" :label="t('trading.volume')" width="80" />
          <el-table-column prop="price" :label="t('trading.price')" width="100" />
          <el-table-column prop="commission" :label="t('backtest.commission')" width="100" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="t('backtest.positions')" name="positions">
        <el-table :data="dailyValues" max-height="400">
          <el-table-column prop="ts" :label="t('backtest.date')" width="120" :formatter="(r, c, v) => (v || '').slice(0, 10)" />
          <el-table-column prop="close" :label="t('backtest.closePrice')" width="100" />
          <el-table-column prop="position" :label="t('backtest.positionQty')" width="100" />
          <el-table-column prop="avg_price" :label="t('backtest.avgPrice')" width="100" />
          <el-table-column :label="t('backtest.marketValue')" width="120">
            <template #default="{ row }">{{ (row.position * row.close).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="cash" :label="t('backtest.cash')" width="120" />
          <el-table-column prop="value" :label="t('backtest.totalValue')" width="120" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="t('backtest.logs')" name="logs">
        <el-table :data="logs" max-height="400">
          <el-table-column prop="ts" :label="t('backtest.logTime')" width="170" />
          <el-table-column prop="level" :label="t('backtest.logLevel')" width="110">
            <template #default="{ row }">
              <el-tag :type="levelTag(row.level)" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="msg" :label="t('backtest.logMsg')" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="t('backtest.rolling')" name="rolling">
        <div style="margin-bottom: 12px">
          <el-select v-model="rollingType" style="width: 180px">
            <el-option v-for="mt in metricTypes" :key="mt.key" :value="mt.key" :label="mt.label" />
          </el-select>
        </div>
        <el-table :data="rollingRows" max-height="400">
          <el-table-column prop="month" :label="t('backtest.date')" width="120" />
          <el-table-column prop="w1" :label="t('backtest.r1m')" />
          <el-table-column prop="w3" :label="t('backtest.r3m')" />
          <el-table-column prop="w6" :label="t('backtest.r6m')" />
          <el-table-column prop="w12" :label="t('backtest.r1y')" />
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import api, { getBacktestRun } from '../api'
import { cssVar } from '../utils/cssVar'

const { t } = useI18n()
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const route = useRoute()
const runId = route.params.id
const symbol = route.params.symbol
const loading = ref(false)
const result = ref({})
const trades = ref([])
const dailyValues = ref([])
const logs = ref([])
const drawdownData = ref([])
const activeTab = ref('equity')
const rollingType = ref('return')
let eventSource = null

// 滚动绩效 type 列表（ptrade 批 3）
const metricTypes = [
  { key: 'return', label: t('backtest.totalReturn') },
  { key: 'benchmark', label: t('backtest.benchmarkReturn') },
  { key: 'alpha', label: t('backtest.alpha') },
  { key: 'beta', label: t('backtest.beta') },
  { key: 'sharpe', label: t('backtest.sharpe') },
  { key: 'sortino', label: t('backtest.sortino') },
  { key: 'information', label: t('backtest.informationRatio') },
  { key: 'volatility', label: t('backtest.volatility') },
  { key: 'drawdown', label: t('backtest.maxDrawdown') },
]

const _fmt = v => (v === null || v === undefined) ? '-' : v
const _pct = v => (v === null || v === undefined) ? '-' : `${v}%`

// 指标概览 10 项
const overviewMetrics = computed(() => {
  const r = result.value
  return [
    { key: 'total_return', label: t('backtest.totalReturn'), value: _pct(r.total_return_pct) },
    { key: 'benchmark', label: t('backtest.benchmarkReturn'), value: _pct(r.benchmark_return) },
    { key: 'alpha', label: t('backtest.alpha'), value: _fmt(r.alpha) },
    { key: 'beta', label: t('backtest.beta'), value: _fmt(r.beta) },
    { key: 'sharpe', label: t('backtest.sharpe'), value: _fmt(r.sharpe_ratio) },
    { key: 'sortino', label: t('backtest.sortino'), value: _fmt(r.sortino_ratio) },
    { key: 'information', label: t('backtest.informationRatio'), value: _fmt(r.information_ratio) },
    { key: 'volatility', label: t('backtest.volatility'), value: _pct(r.volatility) },
    { key: 'benchmark_volatility', label: t('backtest.benchmarkVolatility'), value: _pct(r.benchmark_volatility) },
    { key: 'max_drawdown', label: t('backtest.maxDrawdown'), value: _pct(r.max_drawdown_pct) },
  ]
})

// 滚动绩效二维表（行=月，列=窗口）
const _rollingMetricKey = {
  return: 'return', benchmark: 'benchmark_return', alpha: 'alpha', beta: 'beta',
  sharpe: 'sharpe', sortino: 'sortino', information: 'information_ratio',
  volatility: 'volatility', drawdown: 'max_drawdown',
}
const _pctKeys = { return: true, benchmark: true, volatility: true, drawdown: true }
const rollingRows = computed(() => {
  const rolling = result.value.metrics?.rolling || {}
  const key = _rollingMetricKey[rollingType.value] || 'return'
  const fmt = _pctKeys[rollingType.value] ? _pct : _fmt   // 百分数指标加 %（盲审 P2）
  return Object.entries(rolling).sort(([a], [b]) => a.localeCompare(b)).map(([month, windows]) => ({
    month,
    w1: fmt(windows['1']?.[key]),
    w3: fmt(windows['3']?.[key]),
    w6: fmt(windows['6']?.[key]),
    w12: fmt(windows['12']?.[key]),
  }))
})

const levelTag = level => ({ WARNING: 'warning', ERROR: 'danger', CRITICAL: 'danger' }[level] || 'info')

const exportReport = async () => {
  try {
    const r = await api.get(`/backtest/${runId}/export`, { params: { symbol }, responseType: 'blob' })
    const url = URL.createObjectURL(r)   // axios 拦截器 res=>res.data，r 已是 Blob（盲审 P0/P1）
    const a = document.createElement('a')
    a.href = url
    a.download = `backtest_${runId}_${symbol}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)   // 延迟 revoke 防 Firefox 截断下载
  } catch { ElMessage.error(t('common.failed')) }
}

const equityOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: [t('trading.equity')] },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: dailyValues.value.map(d => d.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{
    name: t('trading.equity'), type: 'line', data: dailyValues.value.map(d => d.value),
    smooth: true, lineStyle: { width: 2,
            markPoint: {
              data: [
                ...(buyPoints.value || []).map(p => ({ coord: [p.ts, p.price], value: 'B', itemStyle: { color: cssVar('--up') } })),
                ...(sellPoints.value || []).map(p => ({ coord: [p.ts, p.price], value: 'S', itemStyle: { color: cssVar('--down') } })),
              ],
              symbolSize: 30,
            }}, areaStyle: { opacity: 0.1 },
  }],
}))

const drawdownOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: drawdownData.value.map(d => d.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ name: t('backtest.drawdown'), type: 'line', data: drawdownData.value.map(d => d.dd), smooth: true, lineStyle: { width: 2, color: cssVar('--down') }, areaStyle: { opacity: 0.1, color: cssVar('--down') } }],
}))

onMounted(async () => {
  loading.value = true
  try {
    const data = await getBacktestRun(runId)
    const symData = data.symbols?.find(s => s.symbol === symbol) || {}
    result.value = symData.result || {}
    trades.value = symData.result?.trades || []
    dailyValues.value = symData.result?.daily_values || []
    logs.value = symData.result?.logs || []
    let peak = 0
    drawdownData.value = (result.value.daily_values || []).map(d => {
      const v = d.value
      if (v > peak) peak = v
      return { ts: d.ts, dd: peak > 0 ? ((peak - v) / peak * 100) : 0 }
    })

    if (data.status === 'running') {
      eventSource = new EventSource(`/api/backtest/${runId}/${symbol}/stream`)
      eventSource.onmessage = (e) => {
        try {
          const frame = JSON.parse(e.data)
          if (frame.error) { eventSource.close(); eventSource = null; return }
          if (frame.daily_values) { dailyValues.value = frame.daily_values }
          if (frame.trades) { trades.value = frame.trades }
          if (frame.total_return_pct !== undefined) {
            result.value = frame
            if (frame.logs) { logs.value = frame.logs }   // 终帧喂 logs（盲审 P2：运行中跑完日志 tab 恒空）
            eventSource.close(); eventSource = null
          }
        } catch (e) { /* ignore malformed frame */ }
      }
    }
  } catch (e) { ElMessage.error(t('backtest.loadViewFailed')) }
  finally { loading.value = false }
})

onUnmounted(() => { if (eventSource) { eventSource.onmessage = null; eventSource.close(); eventSource = null } })

// P2-5(05 §5.7):B/S 买卖点 markPoint
const buyPoints = computed(() => (trades.value || []).filter(t => t.action === 'BUY').map(t => ({ ts: (t.ts || '').slice(0, 10), price: t.price })))
const sellPoints = computed(() => (trades.value || []).filter(t => t.action === 'SELL').map(t => ({ ts: (t.ts || '').slice(0, 10), price: t.price })))

</script>


<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: var(--text-secondary); font-size: 12px; }
.stat .value { font-size: 20px; font-weight: bold; color: var(--text-primary); margin-top: 4px; }
</style>
