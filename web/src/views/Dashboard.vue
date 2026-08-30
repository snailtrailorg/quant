<template>
  <div>
    <!-- P1-3（web-design 05 §5.1）指挥中心：30 秒回答四问——钱安全吗/任务活着吗/今天赚亏/数据正常吗 -->
    <!-- 告警条：critical/warn 未读置顶 -->
    <el-alert v-if="alerts.length" type="error" show-icon :closable="false" style="margin-bottom: 16px">
      <template #title>
        {{ t('dashboard.alertBar', { n: alerts.length }) }}
        <el-button size="small" text type="primary" @click="$router.push('/monitoring')" style="margin-left: 8px">{{ t('dashboard.handleNow') }}</el-button>
      </template>
    </el-alert>

    <!-- 空态三步引导（05 §5.0-1：零数据首访态） -->
    <el-card v-if="emptyState" style="margin-bottom: 20px">
      <div style="text-align: center; padding: 24px 0">
        <div style="font-size: var(--fs-page); font-weight: 600; margin-bottom: 16px">{{ t('dashboard.welcome') }}</div>
        <el-steps :active="emptyStep" align-center style="max-width: 720px; margin: 0 auto">
          <el-step :title="t('dashboard.step1')" :description="t('dashboard.step1d')" />
          <el-step :title="t('dashboard.step2')" :description="t('dashboard.step2d')" />
          <el-step :title="t('dashboard.step3')" :description="t('dashboard.step3d')" />
        </el-steps>
        <div style="margin-top: 20px">
          <el-button type="primary" @click="$router.push('/factors')">{{ t('dashboard.goFactors') }}</el-button>
          <el-button @click="$router.push('/strategy')">{{ t('dashboard.goStrategy') }}</el-button>
          <el-button @click="$router.push('/backtest')">{{ t('dashboard.goBacktest') }}</el-button>
        </div>
      </div>
    </el-card>

    <!-- KPI5 -->
    <el-row :gutter="16">
      <el-col :span="5"><el-card shadow="never"><div class="kpi">
        <div class="klabel">{{ t('trading.totalAssets') }}</div>
        <div class="kpi-num">{{ fmtMoney(dashboard.total_value) }}</div>
        <div class="sparkline">{{ sparkline }}</div>
      </div></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><div class="kpi">
        <div class="klabel">{{ t('trading.todayPnl') }}</div>
        <div class="kpi-num" :class="pnlClass(dashboard.daily_pnl)">{{ pnlArrow(dashboard.daily_pnl) }} {{ fmtMoney(dashboard.daily_pnl) }}</div>
      </div></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><div class="kpi">
        <div class="klabel">{{ t('trading.totalPnl') }}（{{ t('dashboard.sinceInception') }}）</div>
        <div class="kpi-num" :class="pnlClass(dashboard.total_pnl)">{{ pnlArrow(dashboard.total_pnl) }} {{ fmtMoney(dashboard.total_pnl) }}</div>
      </div></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><div class="kpi">
        <div class="klabel">{{ t('dashboard.riskGauge') }}</div>
        <div class="kpi-num" :style="{ color: gaugeColor }">{{ ((riskMetrics.total_drawdown || 0) * 100).toFixed(1) }}%</div>
        <el-progress :percentage="ddPct" :color="gaugeColor" :stroke-width="8" :show-text="false" style="margin-top: 4px" />
      </div></el-card></el-col>
      <el-col :span="4"><el-card shadow="never"><div class="kpi">
        <div class="klabel">{{ t('dashboard.tasksRunning') }}</div>
        <div class="kpi-num">{{ liveTasks.filter(x => x.status === 'running').length }}/{{ liveTasks.length }}</div>
      </div></el-card></el-col>
    </el-row>

    <!-- 权益曲线 + 实盘任务 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="15">
        <el-card shadow="never">
          <template #header>
            <div style="display:flex; justify-content:space-between; align-items:center">
              {{ t('dashboard.equityCurve') }}
              <el-radio-group v-model="curveRange" size="small">
                <el-radio-button v-for="r in ['1D','7D','1M','ALL']" :key="r" :value="r">{{ r }}</el-radio-button>
              </el-radio-group>
            </div>
          </template>
          <v-chart v-if="rangeCurve.length" :option="curveOption" autoresize style="height: 280px" />
          <div v-else class="empty-cell">{{ t('dashboard.noCurve') }}</div>
        </el-card>
      </el-col>
      <el-col :span="9">
        <el-card shadow="never">
          <template #header><div style="display:flex; justify-content:space-between">{{ t('dashboard.liveTasks') }}<el-button text size="small" @click="$router.push('/live-task')">{{ t('dashboard.more') }}→</el-button></div></template>
          <div v-for="task in liveTasks.slice(0, 5)" :key="task.id" class="task-row">
            <span class="dot" :class="task.status" />{{ task.name }}
            <span style="color: var(--text-secondary)">{{ task.symbol }}</span>
            <el-tag size="small" :type="{ running: 'success', stopped: 'info', error: 'danger', frozen: 'primary', pending: 'warning' }[task.status] || 'info'">{{ task.status }}</el-tag>
          </div>
          <div v-if="!liveTasks.length" class="empty-cell">{{ t('dashboard.noTasks') }}</div>
        </el-card>
        <!-- 今日事件（简化：今日 risk/data 类通知） -->
        <el-card shadow="never" style="margin-top: 16px">
          <template #header>{{ t('dashboard.todayEvents') }}</template>
          <div v-for="n in todayEvents.slice(0, 4)" :key="n.id" class="evt-row">⚠ {{ n.title }}</div>
          <div v-if="!todayEvents.length" class="empty-cell">{{ t('dashboard.noEvents') }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 底排：持仓 Top5 / 今日订单流 / 数据健康 / 最近回测 -->
    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :span="6"><el-card shadow="never">
        <template #header><div style="display:flex; justify-content:space-between">{{ t('dashboard.topPositions') }}<el-button text size="small" @click="$router.push('/trading')">{{ t('dashboard.more') }}→</el-button></div></template>
        <div v-for="p in topPositions" :key="p.symbol" class="pos-row">
          <span>{{ (p.symbol || '').split('.')[0] }}</span>
          <span class="num" :class="pnlClass(p.pnl)">{{ pnlArrow(p.pnl) }} {{ fmtMoney(p.pnl) }}</span>
        </div>
        <div v-if="!topPositions.length" class="empty-cell">{{ t('dashboard.noPositions') }}</div>
      </el-card></el-col>
      <el-col :span="6"><el-card shadow="never">
        <template #header>{{ t('dashboard.todayOrders') }}</template>
        <div v-for="o in todayOrders.slice(0, 6)" :key="o.id" class="evt-row">
          <el-tag size="small" :type="o.action === 'BUY' ? 'danger' : 'success'">{{ o.action === 'BUY' ? t('dashboard.buy') : t('dashboard.sell') }}</el-tag>
          {{ (o.symbol || '').split('.')[0] }} ×{{ o.volume }}
          <span style="color: var(--text-secondary)">{{ (o.ts || '').slice(5, 16) }}</span>
        </div>
        <div v-if="!todayOrders.length" class="empty-cell">{{ t('dashboard.noOrders') }}</div>
      </el-card></el-col>
      <el-col :span="6"><el-card shadow="never">
        <template #header><div style="display:flex; justify-content:space-between">{{ t('dashboard.dataHealth') }}<el-button text size="small" @click="$router.push('/data-integrity')">{{ t('dashboard.more') }}→</el-button></div></template>
        <div class="evt-row">{{ t('dashboard.complete') }}: <b class="num" style="color: var(--success)">{{ integrity.complete || 0 }}</b></div>
        <div class="evt-row">{{ t('dashboard.missing') }}: <b class="num" style="color: var(--critical)">{{ integrity.missing || 0 }}</b></div>
        <div class="evt-row" style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('dashboard.dataHealthNote') }}</div>
      </el-card></el-col>
      <el-col :span="6"><el-card shadow="never">
        <template #header><div style="display:flex; justify-content:space-between">{{ t('dashboard.recentBacktests') }}<el-button text size="small" @click="$router.push('/backtest')">{{ t('dashboard.more') }}→</el-button></div></template>
        <div v-for="b in recentBacktests.slice(0, 4)" :key="b.id" class="evt-row">
          #{{ b.id }}
          <span :class="b.status === 'done' ? 'up' : ''">{{ b.summary?.total_return != null ? (b.summary.total_return * 100).toFixed(1) + '%' : b.status }}</span>
          <span style="color: var(--text-secondary)">{{ (b.created_at || '').slice(5, 10) }}</span>
        </div>
        <div v-if="!recentBacktests.length" class="empty-cell">{{ t('dashboard.noBacktests') }}</div>
      </el-card></el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getStrategies, getDashboard, getPnl, getOrders, getLiveTasks,
         getNotifications, getDataIntegrity, getBacktests, getRiskState } from '../api'
import api from '../api'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const { t } = useI18n()
const strategies = ref([])
const dashboard = ref({})
const curve = ref([])
const positions = ref([])
const orders = ref([])
const liveTasks = ref([])
const alerts = ref([])
const todayEvents = ref([])
const integrity = ref({})
const recentBacktests = ref([])
const riskMetrics = ref({})

const pnlClass = v => (v || 0) >= 0 ? 'up' : 'down'
const pnlArrow = v => (v || 0) >= 0 ? '▲' : '▼'
const fmtMoney = v => {
  const n = Math.abs(Number(v) || 0)
  if (n >= 1e8) return `¥${(n / 1e8).toFixed(2)}亿`
  if (n >= 1e4) return `¥${(n / 1e4).toFixed(1)}万`
  return `¥${n.toFixed(0)}`
}
const ddPct = computed(() => Math.min((riskMetrics.value.total_drawdown || 0) / 0.15 * 100, 100))
const gaugeColor = computed(() => ddPct.value >= 90 ? 'var(--critical)' : ddPct.value >= 75 ? 'var(--warn-fill)' : 'var(--success)')

const topPositions = computed(() => [...(positions.value || [])]
  .sort((a, b) => Math.abs(b.pnl || 0) - Math.abs(a.pnl || 0)).slice(0, 5))
const todayOrders = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  return (orders.value || []).filter(o => (o.ts || '').startsWith(today))
})
const emptyState = computed(() => !liveTasks.value.length && !recentBacktests.value.length)
const emptyStep = computed(() => !strategies.value.length ? 0 : recentBacktests.value.length ? 3 : 1)

const curveRange = ref('1M')
const rangeCurve = computed(() => {
  const now = new Date(); const days = { '1D': 1, '7D': 7, '1M': 30, 'ALL': 9999 }[curveRange.value] || 30
  const cutoff = new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10)
  return curve.value.filter(c => (c.ts || '').slice(0, 10) >= cutoff)
})
// 回撤序列(从峰值)
const drawdownSeries = computed(() => {
  let peak = -Infinity
  return rangeCurve.value.map(c => { peak = Math.max(peak, c.value); return peak > 0 ? (c.value - peak) / peak * 100 : 0 })
})
const curveOption = computed(() => ({
  grid: { left: 60, right: 16, top: 16, bottom: 28 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: curve.value.map(c => (c.ts || '').slice(5, 10)) },
  yAxis: { type: 'value', scale: true, axisLabel: { formatter: v => (v / 1e4).toFixed(0) + '万' } },
  yAxis: [{ type: 'value', scale: true }, { type: 'value', name: 'DD%', max: 0, splitLine: { show: false } }],
  series: [
    { type: 'line', data: rangeCurve.value.map(c => c.value), smooth: true, showSymbol: false,
      lineStyle: { width: 2 }, areaStyle: { opacity: 0.08 } },
    { type: 'line', yAxisIndex: 1, data: drawdownSeries.value, showSymbol: false,
      lineStyle: { width: 1, color: '#909399' }, areaStyle: { opacity: 0.15, color: '#909399' } },
  ],
}))

onMounted(async () => {
  const jobs = [
    async () => { strategies.value = await getStrategies() },
    async () => { dashboard.value = await getDashboard() },
    async () => { const p = await getPnl(); curve.value = p.curve || [] },
    async () => { positions.value = (await api.get('/position')).positions || [] },
    async () => { orders.value = (await getOrders()).orders || [] },   // A-P0-1:后端返 {orders,total}
    async () => { liveTasks.value = await getLiveTasks() },
    async () => { const n = await getNotifications('active', 50)
                  const items = n.items || n || []
                  alerts.value = items.filter(x => ['critical', 'warn', 'error'].includes(x.level || x.severity || ''))
                  todayEvents.value = items.filter(x => ['risk', 'data'].includes(x.category)) },
    async () => { integrity.value = (await getDataIntegrity('1D'))?.summary || await getDataIntegrity('1D') },   // A-P2-9:大小写+summary 形状
    async () => { recentBacktests.value = (await getBacktests()).slice(0, 4) },
    async () => { const r = await getRiskState(); riskMetrics.value = r.metrics || {} },
  ]
  jobs.forEach(fn => fn().catch(() => {}))
})
</script>

<style scoped>
.kpi { padding: 6px 0; }
.sparkline { font-size: 12px; color: var(--text-secondary); }
.klabel { color: var(--text-secondary); font-size: var(--fs-label); }
.task-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border-weak); font-size: var(--fs-body); }
.task-row:last-child { border-bottom: none; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.running { background: var(--success); }
.dot.stopped { background: var(--flat); }
.dot.error { background: var(--critical); }
.evt-row { padding: 5px 0; font-size: var(--fs-body); border-bottom: 1px solid var(--border-weak); display: flex; gap: 6px; align-items: center; }
.evt-row:last-child { border-bottom: none; }
.pos-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border-weak); font-size: var(--fs-body); }
.pos-row:last-child { border-bottom: none; }
.empty-cell { color: var(--text-secondary); font-size: var(--fs-body); padding: 18px 0; text-align: center; }
</style>

// KPI sparkline(7 日,文本近似——图表 sparkline 留后续;05 §4.4 KPI=数字+环比箭头+趋势)
const sparkline = computed(() => {
  const vals = curve.value.slice(-7).map(c => c.value)
  if (vals.length < 2) return ''
  const up = vals[vals.length - 1] >= vals[0]
  const pct = ((vals[vals.length - 1] - vals[0]) / vals[0] * 100).toFixed(1)
  return `${up ? '↗' : '↘'} ${pct}%`
})
