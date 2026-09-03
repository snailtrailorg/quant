<template>
  <div v-loading="loading">
    <!-- 头部：快照 + 涨跌停空间 + 五档（8 内容区之 2 常驻） -->
    <el-card>
      <template #header>
          <el-button type="primary" size="small" @click="goBack" style="margin-right: 8px">← {{ t('common.back') }}</el-button>
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div>
            <span style="font-size:18px;font-weight:600">{{ detail.name || quote?.name || symbol }}</span>
            <el-tag v-if="detail.in_pool" type="success" style="margin-left:var(--sp-2)">{{ t('stockDetail.inPool') }}</el-tag>
            <el-tag v-else type="info" style="margin-left:var(--sp-2)">{{ t('stockDetail.notPool') }}</el-tag>
            <span style="margin-left:12px;color:var(--text-secondary)">{{ detail.ts_code }}
              <template v-if="detail.industry"> · {{ detail.industry }}</template>
            </span>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <el-tag :type="quote?.source === 'hub' ? 'success' : 'warning'">
              {{ quote?.source === 'hub' ? t('stockDetail.srcHub') : (quote ? t('stockDetail.srcTencent') : t('stockDetail.noQuote')) }}
            </el-tag>
            <el-button type="primary" @click="refreshAll">{{ t('common.refresh') }}</el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="20">
        <el-col :xs="24" :md="14">
          <div v-if="quote" class="snap">
            <span class="price" :class="priceClass">{{ quote.last }}{{ quote.last > quote.pre_close ? '▲' : quote.last < quote.pre_close ? '▼' : '' }}</span>
            <span class="chg" :class="priceClass">
              {{ quote.chg ?? '-' }} ({{ pctChg == null ? '-' : pctChg.toFixed(2) }}%)
            </span>
            <div class="meta">
              <span>{{ t('stockDetail.turnover') }}: {{ quote.turnover_rate ?? '-' }}%</span>
              <span>{{ t('stockDetail.volume') }}: {{ fmtVol(quote.volume) }}</span>
              <span>{{ t('stockDetail.amount') }}: {{ fmtAmt(quote.amount) }}</span>
              <span>{{ t('stockDetail.high') }}/{{ t('stockDetail.low') }}: {{ quote.high }}/{{ quote.low }}</span>
            </div>
            <div class="meta" style="margin-top:6px">
              <span>{{ t('stockDetail.limitUp') }}: <b style="color:var(--up)">{{ quote.upper_limit ?? detail.limit?.up_limit ?? '-' }}</b></span>
              <span style="margin-left:var(--sp-4)">{{ t('stockDetail.limitDown') }}: <b style="color:var(--down)">{{ quote.lower_limit ?? detail.limit?.down_limit ?? '-' }}</b></span>
              <span style="margin-left:var(--sp-4);color:var(--text-secondary)">{{ quote.ts }}</span>
            </div>
          </div>
          <el-empty v-else :description="t('stockDetail.noQuote')" :image-size="60" />
        </el-col>
        <el-col :xs="24" :md="10">
          <table v-if="quote?.bid?.length" class="depth">
            <tbody>
              <tr v-for="i in 5" :key="'a' + i" class="ask">
                <td>{{ t('stockDetail.ask') }}{{ 6 - i }}</td>
                <td>{{ fmtDepth(quote.ask?.[5 - i]) }}</td>
                <td>{{ fmtDepth(quote.ask_v?.[5 - i]) }}</td>
              </tr>
              <tr><td colspan="3" class="mid">—— {{ quote.last }} ——</td></tr>
              <tr v-for="i in 5" :key="'b' + i" class="bid">
                <td>{{ t('stockDetail.bid') }}{{ i }}</td>
                <td>{{ fmtDepth(quote.bid?.[i - 1]) }}</td>
                <td>{{ fmtDepth(quote.bid_v?.[i - 1]) }}</td>
              </tr>
            </tbody>
          </table>
        </el-col>
      </el-row>
    </el-card>

    <!-- Tab（8 内容区之 6） -->
    <el-card style="margin-top:var(--sp-4)">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('stockDetail.kline')" name="kline">
          <div style="margin-bottom:var(--sp-2)">
            <el-radio-group v-model="klineMode" @change="onKlineMode">
              <el-radio-button label="day">{{ t('stockDetail.kDay') }}</el-radio-button>
              <el-radio-button label="intraday">{{ t('stockDetail.kIntraday') }}</el-radio-button>
            </el-radio-group>
            <span v-if="intraday.date" style="margin-left:12px;color:var(--text-secondary)">
              {{ intraday.date }}（{{ srcName(intraday.source) }}）
            </span>
          </div>
          <!-- P2-13（09-B9）：吸收 KlineDialog 指标全集（MA/BOLL/SAR 主图+7 副图指标） -->
          <el-button size="small" @click="klineDlg = true" style="margin-left: 12px">{{ t('stockDetail.fullChart') }}</el-button>
          <KlineDialog v-model="klineDlg" :symbol="symbol" />
          <template v-if="klineMode === 'day'">
            <v-chart v-if="klineData.length" :option="klineOption" autoresize style="height:420px" />
            <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
          </template>
          <template v-else>
            <v-chart v-if="intraday.points?.length" :option="intradayOption" autoresize style="height:420px" v-loading="intradayLoading" />
            <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" v-loading="intradayLoading" />
          </template>
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.moneyflow')" name="moneyflow">
          <v-chart v-if="moneyflow.length" :option="flowOption" autoresize style="height:360px" />
          <el-empty v-else :description="t('stockDetail.noData')" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.chips')" name="chips">
          <div v-if="detail.chips" style="color:var(--text-secondary);margin-bottom:6px">
            {{ t('stockDetail.chipsDate') }}: {{ detail.chips.trade_date }}（{{ detail.chips.source }}）
          </div>
          <v-chart v-if="detail.chips?.dist?.length" :option="chipsOption" autoresize style="height:360px" />
          <el-empty v-else :description="t('stockDetail.noChips')" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane :label="t('stockDetail.events')" name="events">
          <el-table :data="detail.events || []" max-height="420">
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
                  <template #header>{{ t('stockDetail.fIncome') }}（{{ detail.finance.income?.ann_date || '-' }}）</template>
                  <div class="frow"><span>{{ t('stockDetail.revenue') }}</span><b>{{ yi(detail.finance.income?.total_revenue) }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.netProfit') }}</span><b>{{ yi(detail.finance.income?.n_income) }}</b></div>
                  <div class="frow"><span>EPS</span><b>{{ detail.finance.income?.basic_eps ?? '-' }}</b></div>
                  <div class="frow"><span>{{ t('stockDetail.rd') }}</span><b>{{ yi(detail.finance.income?.rd_exp) }}</b></div>
                </el-card>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-card shadow="hover">
                  <template #header>{{ t('stockDetail.fIndicator') }}（{{ detail.finance.indicator?.end_date || '-' }}）</template>
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
            <span v-if="!canAnalyze" style="color:var(--text-secondary);align-self:center">{{ t('stockDetail.analyzePerm') }}</span>
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
import KlineDialog from '../components/KlineDialog.vue'
import { ElMessage } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { stockDetail, stockAnalyze, getKline, stockIntraday, apiErr } from '../api'

const { t } = useI18n()
const goBack = () => {
  // wd-20 §2.6 返回上下文：query.from 存在则回对应 tab，否则 router.back()
  const from = route.query.from
  if (from) { router.push({ path: String(from), query: { tab: route.query.tab } }); return }
  router.back()
}
use([CanvasRenderer, BarChart, LineChart, CandlestickChart,
     GridComponent, TooltipComponent, LegendComponent, DataZoomComponent, MarkLineComponent])

const route = useRoute()
const symbol = String(route.params.symbol || '')
const klineDlg = ref(false)
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
let detailSeq = 0, intradaySeq = 0
const intradayLoading = ref(false)

const EVENT_TAG = { top_list: 'danger', block_trade: 'warning', share_float: 'primary', pledge: 'info' }

// 补盲审 S1：hub 源 tick 无 pct_chg（vnpy 只有 pre_close）——缺失时用 last/pre_close 计算，
// 否则 ?? 0 恒判涨红。平盘/未知归中性灰。
const pctChg = computed(() => {
  const q = quote.value
  if (!q) return null
  if (q.pct_chg != null) return q.pct_chg
  if (q.last != null && q.pre_close > 0) return (q.last / q.pre_close - 1) * 100
  return null
})
const priceClass = computed(() => {
  const p = pctChg.value
  return p == null ? 'flat' : p > 0 ? 'up' : p < 0 ? 'down' : 'flat'
})
const fmtDepth = v => (v > 0 ? v : '-')   // B8：hub 缺档价 0 不显示假档位
const srcName = s => s === 'hub' ? t('stockDetail.srcHub') : s === 'tencent' ? t('stockDetail.srcTencent') : (s || '-')

const fmtVol = v => v == null ? '-' : (v >= 1e8 ? (v / 1e8).toFixed(2) + t('stockDetail.uYi') + t('stockDetail.uGu') : v >= 1e4 ? (v / 1e4).toFixed(2) + t('stockDetail.uWan') + t('stockDetail.uGu') : v + t('stockDetail.uGu'))
const fmtAmt = v => v == null ? '-' : (v >= 1e8 ? (v / 1e8).toFixed(2) + t('stockDetail.uYi') + t('stockDetail.uYuan') : v >= 1e4 ? (v / 1e4).toFixed(2) + t('stockDetail.uWan') + t('stockDetail.uYuan') : v + t('stockDetail.uYuan'))
const yi = v => v == null ? '-' : (v / 1e8).toFixed(2) + ' ' + t('stockDetail.uYi')
const pct = v => v == null ? '-' : v + '%'

function eventText(row) {
  const U = { gu: t('stockDetail.uGu'), wan: t('stockDetail.uWan'), yuan: t('stockDetail.uYuan'), bi: t('stockDetail.uBi') }
  if (row.type === 'top_list') return `${row.detail || ''} ${t('stockDetail.evClose')} ${row.close ?? '-'} ${t('stockDetail.evNet')} ${row.net_amount ?? '-'}${U.wan}`
  if (row.type === 'block_trade') return `${row.price ?? '-'}${U.yuan} ${row.vol ?? '-'}${U.wan}${U.gu} ${row.amount ?? '-'}${U.wan}${U.yuan} ${row.buyer || ''} → ${row.seller || ''}`
  if (row.type === 'share_float') return `${row.float_share ?? '-'}${U.wan}${U.gu} (${row.float_ratio ?? '-'}%) ${row.holder || ''}`
  if (row.type === 'pledge') return `${t('stockDetail.ev_pledge')}: ${row.pledge_count ?? '-'} ${U.bi}, ${row.pledge_ratio ?? '-'}%`
  return ''
}

function refreshAll() {
  fetchDetail()
  fetchKline()
  if (klineMode.value === 'intraday') fetchIntraday()
}

async function fetchDetail(silent = false) {
  // 补盲审 G1/G2：轮询静默（不遮罩不 toast，防 30s 风暴/闪屏）；B7：序号守卫防乱序覆盖
  const seq = ++detailSeq
  if (!silent) loading.value = true
  try {
    const d = await stockDetail(symbol)
    if (seq === detailSeq) detail.value = d
  } catch (e) {
    if (!silent && seq === detailSeq) ElMessage.error(apiErr(e))
  } finally {
    if (!silent) loading.value = false
  }
}

async function fetchKline() {
  try {
    const rows = await getKline(symbol, 120)
    klineData.value = rows || []
  } catch { klineData.value = [] }
}

async function fetchIntraday(silent = false) {
  const seq = ++intradaySeq
  if (!silent) intradayLoading.value = true
  try {
    const d = await stockIntraday(symbol) || {}
    if (seq === intradaySeq) intraday.value = d
  } catch { /* 分时失败保留旧数据 */ }
  finally { if (!silent) intradayLoading.value = false }
}

function onKlineMode(m) {
  if (m === 'intraday' && !intraday.value.points?.length) fetchIntraday()
}

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
  const up = 'var(--up)', down = 'var(--down)'
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
  const up = 'var(--up)', down = 'var(--down)'
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
          lineStyle: { color: 'var(--text-secondary)', type: 'dashed' },
          data: [{ yAxis: first }], label: { formatter: String(first) } } },
      { name: t('stockDetail.avgPrice'), type: 'line', data: pts.map(p => p.avg), showSymbol: false,
        lineStyle: { width: 1, color: 'var(--ma-line)' }, itemStyle: { color: 'var(--ma-line)' } },
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
      itemStyle: { color: 'var(--brand-600)' },
      markLine: quote.value?.last ? { symbol: 'none', data: [{ xAxis: nearestChipIdx(dist, quote.value.last), name: 'last' }],
        lineStyle: { color: 'var(--up)', type: 'dashed' }, label: { formatter: String(quote.value.last) } } : undefined,
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
  // 补盲审 S2：分时模式活跃时轮询也刷（原只拉一次当天冻结）；G2：轮询静默
  timer = setInterval(() => {
    fetchDetail(true)
    if (klineMode.value === 'intraday') fetchIntraday(true)
  }, 30000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.snap .price { font-size: 34px; font-weight: 700; margin-right: 10px; }
.snap .chg { font-size: 16px; font-weight: 600; }
.up { color: var(--up); }
.down { color: var(--down); }
.flat { color: var(--text-secondary); }
.meta { display: flex; gap: 18px; color: var(--text-secondary); flex-wrap: wrap; font-size: 13px; margin-top: 10px; }
.depth { width: 100%; border-collapse: collapse; font-size: 13px; }
.depth td { padding: 2px 8px; border-bottom: 1px solid var(--border-weak); }
.depth .mid { text-align: center; color: var(--text-secondary); font-weight: 600; padding: 4px 0; }
.depth .ask td:nth-child(2) { color: var(--down); }
.depth .bid td:nth-child(2) { color: var(--up); }
.frow { display: flex; justify-content: space-between; padding: 4px 0; }
.analysis { white-space: pre-wrap; line-height: 1.7; background: #f8f9fb; padding: 14px; border-radius: 6px; }
</style>
