<template>
  <div v-loading="loading">
    <!-- 头部：快照 + 涨跌停空间 + 五档（8 内容区之 2 常驻） -->
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div>
            <span style="font-size:18px;font-weight:600">{{ detail.name || quote?.name || symbol }}</span>
            <el-tag v-if="detail.in_pool" type="success" style="margin-left:8px">{{ t('stockDetail.inPool') }}</el-tag>
            <el-tag v-else type="info" style="margin-left:8px">{{ t('stockDetail.notPool') }}</el-tag>
            <span style="margin-left:12px;color:#909399">{{ detail.ts_code }}
              <template v-if="detail.industry"> · {{ detail.industry }}</template>
            </span>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <el-tag :type="quote?.source === 'hub' ? 'success' : 'warning'">
              {{ quote?.source === 'hub' ? t('stockDetail.srcHub') : (quote ? t('stockDetail.srcTencent') : t('stockDetail.noQuote')) }}
            </el-tag>
            <el-button type="primary" @click="fetchDetail">{{ t('common.refresh') }}</el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="20">
        <el-col :xs="24" :md="14">
          <div v-if="quote" class="snap">
            <span class="price" :class="priceClass">{{ quote.last }}</span>
            <span class="chg" :class="priceClass">
              {{ quote.chg ?? '-' }} ({{ quote.pct_chg ?? '-' }}%)
            </span>
            <div class="meta">
              <span>{{ t('stockDetail.turnover') }}: {{ quote.turnover_rate ?? '-' }}%</span>
              <span>{{ t('stockDetail.volume') }}: {{ fmtVol(quote.volume) }}</span>
              <span>{{ t('stockDetail.amount') }}: {{ fmtAmt(quote.amount) }}</span>
              <span>{{ t('stockDetail.high') }}/{{ t('stockDetail.low') }}: {{ quote.high }}/{{ quote.low }}</span>
            </div>
            <div class="meta" style="margin-top:6px">
              <span>{{ t('stockDetail.limitUp') }}: <b style="color:#f56c6c">{{ quote.upper_limit ?? detail.limit?.up_limit ?? '-' }}</b></span>
              <span style="margin-left:16px">{{ t('stockDetail.limitDown') }}: <b style="color:#67c23a">{{ quote.lower_limit ?? detail.limit?.down_limit ?? '-' }}</b></span>
              <span style="margin-left:16px;color:#909399">{{ quote.ts }}</span>
            </div>
          </div>
          <el-empty v-else :description="t('stockDetail.noQuote')" :image-size="60" />
        </el-col>
        <el-col :xs="24" :md="10">
          <table v-if="quote?.bid?.length" class="depth">
            <tbody>
              <tr v-for="i in 5" :key="'a' + i" class="ask">
                <td>{{ t('stockDetail.ask') }}{{ 6 - i }}</td>
                <td>{{ quote.ask?.[5 - i] ?? '-' }}</td>
                <td>{{ quote.ask_v?.[5 - i] ?? '-' }}</td>
              </tr>
              <tr><td colspan="3" class="mid">—— {{ quote.last }} ——</td></tr>
              <tr v-for="i in 5" :key="'b' + i" class="bid">
                <td>{{ t('stockDetail.bid') }}{{ i }}</td>
                <td>{{ quote.bid?.[i - 1] ?? '-' }}</td>
                <td>{{ quote.bid_v?.[i - 1] ?? '-' }}</td>
              </tr>
            </tbody>
          </table>
        </el-col>
      </el-row>
    </el-card>

    <!-- Tab（8 内容区之 6） -->
    <el-card style="margin-top:16px">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('stockDetail.kline')" name="kline">
          <div style="margin-bottom:8px">
            <el-radio-group v-model="klineMode" @change="onKlineMode">
              <el-radio-button label="day">{{ t('stockDetail.kDay') }}</el-radio-button>
              <el-radio-button label="intraday">{{ t('stockDetail.kIntraday') }}</el-radio-button>
            </el-radio-group>
            <span v-if="intraday.date" style="margin-left:12px;color:#909399">
              {{ intraday.date }}（{{ intraday.source }}）
            </span>
          </div>
          <template v-if="klineMode === 'day'">
            <v-chart v-if="klineData.length" :option="klineOption" autoresize style="height:420px" />
            <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
          </template>
          <template v-else>
            <v-chart v-if="intraday.points?.length" :option="intradayOption" autoresize style="height:420px" />
            <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
          </template>
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.moneyflow')" name="moneyflow">
          <v-chart v-if="moneyflow.length" :option="flowOption" autoresize style="height:360px" />
          <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.chips')" name="chips">
          <div v-if="detail.chips" style="color:#909399;margin-bottom:6px">
            {{ t('stockDetail.chipsDate') }}: {{ detail.chips.trade_date }}（{{ detail.chips.source }}）
          </div>
          <v-chart v-if="detail.chips?.dist?.length" :option="chipsOption" autoresize style="height:360px" />
          <el-empty v-else :description="t('stockDetail.noChips')" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.events')" name="events">
          <el-table :data="detail.events || []" stripe max-height="420">
            <el-table-column prop="date" :label="t('stockDetail.date')" width="110" />
            <el-table-column :label="t('stockDetail.eventType')" width="120">
              <template #default="{ row }">
                <el-tag :type="EVENT_TAG[row.type] || 'info'">{{ t('stockDetail.ev_' + row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('stockDetail.eventDetail')" min-width show-overflow-tooltip>
              <template #default="{ row }">{{ eventText(row) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.finance')" name="finance">
          <template v-if="detail.finance">
            <el-row :gutter="16">
              <el-col :xs="24" :md="12">
                <el-card shadow="hover">
                  <template #header>{{ t('stockDetail.fIncome') }}（{{ detail.finance.income?.ann_date }}）</template>
                  <div class="frow"><span>{{ t('stockDetail.revenue') }}</span><b>{{ yi(detail.finance.income?.total_revenue) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.netProfit') }}</span><b>{{ yi(detail.finance.income?.n_income) }}</b></div>
                  <div class="frow"><span>EPS</span><b>{{ detail.finance.income?.basic_eps ?? '-' }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.rd') }}</span><b>{{ yi(detail.finance.income?.rd_exp) }}</b></div>
                </el-card>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-card shadow="hover">
                  <template #header>{{ t('stockDetail.fIndicator') }}（{{ detail.finance.indicator?.end_date }}）</template>
                  <div class="frow"><span>ROE</span><b>{{ pct(detail.finance.indicator?.roe) }}</b></div>
                  <div class="frow"><span>ROA</span><b>{{ pct(detail.finance.indicator?.roa) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.grossMargin') }}</span><b>{{ pct(detail.finance.indicator?.gross_margin) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.debtRatio') }}</span><b>{{ pct(detail.finance.indicator?.debt_to_assets) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.revYoy') }}</span><b>{{ pct(detail.finance.indicator?.revenue_yoy) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.profitYoy') }}</span><b>{{ pct(detail.finance.indicator?.netprofit_yoy) }}</b></div>
                </el-card>
              </el-col>
            </el-row>
          </template>
          <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.ai')" name="ai">
          <div style="margin-bottom:10px;display:flex;gap:8px">
            <el-button type="primary" :loading="analyzing" :disabled="!canAnalyze" @click="doAnalyze">
              {{ analysis ? t('stockDetail.reAnalyze') : t('stockDetail.doAnalyze') }}
            </el-button>
            <span v-if="!canAnalyze" style="color:#909399;align-self:center">{{ t('stockDetail.analyzePerm') }}</span>
          </div>
          <div v-if="analysis" class="analysis">{{ analysis }}</div>
          <el-empty v-else :description="t('stockDetail.aiHint')" :image-size="60" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { stockDetail, stockAnalyze, getKline, stockIntraday, apiErr } from '../api'

const { t } = useI18n()
use([CanvasRenderer, BarChart, LineChart, CandlestickChart,
     GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent])

const route = useRoute()
const symbol = String(route.params.symbol || '')
const loading = ref(false)
const detail = ref({})
const quote = computed(() => detail.value.quote)
const activeTab = ref('kline')
const klineMode = ref('day')
const klineData = ref([])
const intraday = ref({})
const analysis = ref('')
const analyzing = ref(false)
const canAnalyze = ['analyst', 'trader', 'admin'].includes(localStorage.getItem('role') || '')
let timer = null

const EVENT_TAG = { top_list: 'danger', block_trade: 'warning', share_float: 'primary', pledge: 'info' }

const priceClass = computed(() =>
  (quote.value?.pct_chg ?? 0) >= 0 ? 'up' : 'down')   // A 股习惯：红涨绿跌

const fmtVol = v => v == null ? '-' : (v >= 1e8 ? (v / 1e8).toFixed(2) + '亿股' : v >= 1e4 ? (v / 1e4).toFixed(2) + '万股' : v + '股')
const fmtAmt = v => v == null ? '-' : (v >= 1e8 ? (v / 1e8).toFixed(2) + '亿元' : v >= 1e4 ? (v / 1e4).toFixed(2) + '万元' : v + '元')
const yi = v => v == null ? '-' : (v / 1e8).toFixed(2) + ' 亿'
const pct = v => v == null ? '-' : v + '%'

function eventText(row) {
  if (row.type === 'top_list') return `${row.detail || ''} 收 ${row.close ?? '-'} 净买 ${row.net_amount ?? '-'}万`
  if (row.type === 'block_trade') return `${row.price ?? '-'}元 ${row.vol ?? '-'}万股 ${row.amount ?? '-'}万元 ${row.buyer || ''} → ${row.seller || ''}`
  if (row.type === 'share_float') return `${row.float_share ?? '-'}万股 (${row.float_ratio ?? '-'}%) ${row.holder || ''}`
  if (row.type === 'pledge') return `${t('stockDetail.ev_pledge')}: ${row.pledge_count ?? '-'} 笔, ${row.pledge_ratio ?? '-'}%`
  return ''
}

async function fetchDetail() {
  loading.value = true
  try {
    detail.value = await stockDetail(symbol)
  } catch (e) {
    ElMessage.error(apiErr(e))
  } finally {
    loading.value = false
  }
}

async function fetchKline() {
  try {
    const rows = await getKline(symbol, 120)
    klineData.value = rows || []
  } catch { klineData.value = [] }
}

async function fetchIntraday() {
  try {
    intraday.value = await stockIntraday(symbol) || {}
  } catch { intraday.value = {} }
}

function onKlineMode(m) { if (m === 'intraday' && !intraday.value.points?.length) fetchIntraday() }

async function doAnalyze() {
  analyzing.value = true
  try {
    const r = await stockAnalyze(symbol)
    analysis.value = r.analysis || ''
  } catch (e) {
    ElMessage.error(apiErr(e))
  } finally {
    analyzing.value = false
  }
}

const klineOption = computed(() => {
  const up = '#f56c6c', down = '#67c23a'
  return {
    tooltip: { trigger: 'axis' },
    grid: [{ left: '8%', right: '3%', top: '6%', height: '58%' }, { left: '8%', right: '3%', top: '72%', height: '18%' }],
    xAxis: [{ type: 'category', data: klineData.value.map(d => d.ts?.slice(0, 10)), boundaryGap: true },
            { type: 'category', gridIndex: 1, data: klineData.value.map(d => d.ts?.slice(0, 10)), axisLabel: { show: false } }],
    yAxis: [{ type: 'value', scale: true }, { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 }],
    series: [
      { name: 'K', type: 'candlestick',
        data: klineData.value.map(d => [d.open, d.close, d.low, d.high]),
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down } },
      { name: t('stockDetail.volume'), type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: klineData.value.map(d => ({ value: d.volume, itemStyle: { color: d.close >= d.open ? up : down } })) },
    ],
  }
})

const intradayOption = computed(() => {
  const pts = intraday.value.points || []
  const up = '#f56c6c', down = '#67c23a'
  const first = pts[0]?.price ?? 0
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: [t('stockDetail.price'), t('stockDetail.avgPrice')] },
    grid: [{ left: '8%', right: '3%', top: '8%', height: '56%' }, { left: '8%', right: '3%', top: '72%', height: '18%' }],
    xAxis: [{ type: 'category', data: pts.map(p => p.t), boundaryGap: false },
            { type: 'category', gridIndex: 1, data: pts.map(p => p.t), axisLabel: { show: false }, boundaryGap: false }],
    yAxis: [{ type: 'value', scale: true },
            { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
    series: [
      { name: t('stockDetail.price'), type: 'line', data: pts.map(p => p.price), showSymbol: false,
        lineStyle: { width: 1.5 },
        areaStyle: { opacity: 0.06 },
        markLine: { symbol: 'none', silent: true,
          lineStyle: { color: '#909399', type: 'dashed' },
          data: [{ yAxis: first }], label: { formatter: String(first) } } },
      { name: t('stockDetail.avgPrice'), type: 'line', data: pts.map(p => p.avg), showSymbol: false,
        lineStyle: { width: 1, color: '#e6a23c' }, itemStyle: { color: '#e6a23c' } },
      { name: t('stockDetail.volume'), type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: pts.map(p => ({ value: p.volume, itemStyle: { color: p.price >= (p.avg ?? p.price) ? up : down } })) },
    ],
  }
})

const flowOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: [t('stockDetail.buyLg'), t('stockDetail.sellLg'), t('stockDetail.netMf')] },
  grid: { left: '8%', right: '3%', bottom: '10%', containLabel: true },
  xAxis: { type: 'category', data: (detail.value.moneyflow || []).map(d => d.trade_date).reverse() },
  yAxis: { type: 'value' },
  series: [
    { name: t('stockDetail.buyLg'), type: 'bar', data: (detail.value.moneyflow || []).map(d => d.buy_lg).reverse() },
    { name: t('stockDetail.sellLg'), type: 'bar', data: (detail.value.moneyflow || []).map(d => d.sell_lg).reverse() },
    { name: t('stockDetail.netMf'), type: 'line', data: (detail.value.moneyflow || []).map(d => d.net_mf).reverse() },
  ],
}))

const chipsOption = computed(() => {
  const dist = detail.value.chips?.dist || []
  return {
    tooltip: { trigger: 'axis', formatter: ps => `${ps[0].axisValue}<br/>${ps[0].value}%` },
    grid: { left: '8%', right: '3%', bottom: '10%', containLabel: true },
    xAxis: { type: 'category', data: dist.map(d => d[0]) },
    yAxis: { type: 'value', name: '%' },
    series: [{
      type: 'bar', data: dist.map(d => d[1]),
      itemStyle: { color: '#409eff' },
      markLine: quote.value?.last ? { symbol: 'none', data: [{ xAxis: nearestChipIdx(dist, quote.value.last), name: 'last' }],
        lineStyle: { color: '#f56c6c', type: 'dashed' }, label: { formatter: String(quote.value.last) } } : undefined,
    }],
  }
})

function nearestChipIdx(dist, price) {
  let best = 0, gap = Infinity
  dist.forEach((d, i) => { const g = Math.abs(d[0] - price); if (g < gap) { gap = g; best = i } })
  return dist[best]?.[0] ?? ''
}

onMounted(() => {
  fetchDetail()
  fetchKline()
  timer = setInterval(fetchDetail, 30000)   // 盘中 30s 刷新快照
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.snap .price { font-size: 34px; font-weight: 700; margin-right: 10px; }
.snap .chg { font-size: 16px; font-weight: 600; }
.up { color: #f56c6c; }
.down { color: #67c23a; }
.meta { display: flex; gap: 18px; color: #606266; flex-wrap: wrap; font-size: 13px; margin-top: 10px; }
.depth { width: 100%; border-collapse: collapse; font-size: 13px; }
.depth td { padding: 2px 8px; border-bottom: 1px solid #f0f2f5; }
.depth .mid { text-align: center; color: #909399; font-weight: 600; padding: 4px 0; }
.depth .ask td:nth-child(2) { color: #67c23a; }
.depth .bid td:nth-child(2) { color: #f56c6c; }
.frow { display: flex; justify-content: space-between; padding: 4px 0; }
.analysis { white-space: pre-wrap; line-height: 1.7; background: #f8f9fb; padding: 14px; border-radius: 6px; }
</style>
