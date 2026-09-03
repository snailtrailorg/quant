<template>
  <el-card v-loading="loading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('backtest.viewTitle', { symbol }) }}</span>
        <el-button type="primary" @click="$router.back()">{{ t('common.return') }}</el-button>
      </div>
    </template>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.totalReturn') }}</div><div class="value">{{ result.total_return_pct }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.winRate') }}</div><div class="value">{{ result.win_rate }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.sharpe') }}</div><div class="value">{{ result.sharpe_ratio }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.maxDrawdown') }}</div><div class="value">{{ result.max_drawdown_pct }}%</div></div></el-card></el-col>
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
import { getBacktestRun } from '../api'
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
const drawdownData = ref([])
const activeTab = ref('equity')
let eventSource = null

const equityOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: [t('trading.equity')] },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: dailyValues.value.map(d => d.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{
    name: t('trading.equity'), type: 'line', data: dailyValues.value.map(d => d.value),
    smooth: true, lineStyle: { width: 2 ,
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
// P2-5(05 §5.7):同窗实盘净值对照
const livePnl = ref([])
const loadLivePnl = async () => {
  try { const p = await api.get('/pnl'); livePnl.value = (p.curve || []).filter(c => {
    const d = (c.ts || '').slice(0, 10)
    return d >= route.params.start && d <= route.params.end
  }) } catch {}
}

</script>


<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: var(--text-secondary); font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: var(--text-primary); margin-top: 4px; }
</style>
