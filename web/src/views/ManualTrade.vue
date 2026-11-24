<template>
  <div class="mt-page">
    <el-card>
      <template #header>
        <div class="mt-head">
          <span>{{ t('nav.manualTrade') }}</span>
          <span class="mt-hint">{{ t('manualTrade.hint') }}</span>
        </div>
      </template>

      <!-- 接口 + 标的 -->
      <el-row :gutter="16" class="mt-row">
        <el-col :xs="24" :md="8">
          <div class="mt-label">{{ t('manualTrade.account') }}</div>
          <el-select v-model="form.account_id" :placeholder="t('manualTrade.accountPh')" style="width:100%">
            <el-option v-for="a in tdAccounts" :key="a.code" :label="a.code" :value="a.code" />
          </el-select>
        </el-col>
        <el-col :xs="24" :md="10">
          <div class="mt-label">{{ t('manualTrade.symbol') }}</div>
          <el-select v-model="form.symbol" filterable remote :remote-method="searchSymbols"
            :loading="symbolLoading" :placeholder="t('manualTrade.symbolPh')" style="width:100%"
            @change="onSymbolChange" allow-create>
            <el-option v-for="s in symbolOptions" :key="s" :label="s" :value="s" />
          </el-select>
        </el-col>
        <el-col :xs="24" :md="6">
          <div class="mt-label">{{ t('manualTrade.marketHint') }}</div>
          <div class="mt-unit">{{ marketUnit }}</div>
        </el-col>
      </el-row>

      <!-- 行情区（左，同一空间日K/分时切换） + L1 盘口（右，全部可点） -->
      <el-row :gutter="16" class="mt-chart-row">
        <el-col :xs="24" :md="17">
          <div class="mt-chart-head">
            <el-radio-group v-model="chartMode" size="small" @change="onChartMode">
              <el-radio-button value="day">{{ t('manualTrade.dayK') }}</el-radio-button>
              <el-radio-button value="intraday">{{ t('manualTrade.intraday') }}</el-radio-button>
            </el-radio-group>
            <span class="mt-chart-src">{{ quote?.source || '-' }}</span>
          </div>
          <div v-loading="chartLoading" class="mt-chart">
            <v-chart v-if="chartMode==='day' && klineData.length" :option="klineOption" autoresize />
            <v-chart v-else-if="chartMode==='intraday' && intradayPoints.length" :option="intradayOption" autoresize />
            <el-empty v-else :description="t('manualTrade.noChart')" :image-size="50" />
          </div>
        </el-col>
        <el-col :xs="24" :md="7">
          <div class="mt-label">{{ t('manualTrade.depth') }}</div>
          <div v-if="quote?.bid?.length" class="mt-depth">
            <div v-for="i in 5" :key="'a'+i" class="mt-depth-row ask" @click="pickPrice(quote.ask?.[5-i])">
              <span>{{ t('manualTrade.ask') }}{{ 6-i }}</span>
              <b>{{ fmtDepth(quote.ask?.[5-i]) }}</b>
              <span>{{ fmtDepth(quote.ask_v?.[5-i]) }}</span>
            </div>
            <div class="mt-depth-mid">{{ fmtDepth(quote.last) }}</div>
            <div v-for="i in 5" :key="'b'+i" class="mt-depth-row bid" @click="pickPrice(quote.bid?.[i-1])">
              <span>{{ t('manualTrade.bid') }}{{ i }}</span>
              <b>{{ fmtDepth(quote.bid?.[i-1]) }}</b>
              <span>{{ fmtDepth(quote.bid_v?.[i-1]) }}</span>
            </div>
          </div>
          <el-empty v-else :description="t('manualTrade.noDepth')" :image-size="50" />
          <div class="mt-depth-tip">{{ t('manualTrade.depthTip') }}</div>
        </el-col>
      </el-row>

      <!-- 下单 -->
      <el-row :gutter="16" class="mt-row">
        <el-col :xs="24" :md="6">
          <div class="mt-label">{{ t('manualTrade.action') }}</div>
          <el-radio-group v-model="form.action">
            <el-radio-button value="BUY">{{ t('dashboard.buy') }}</el-radio-button>
            <el-radio-button value="SELL">{{ t('dashboard.sell') }}</el-radio-button>
          </el-radio-group>
        </el-col>
        <el-col :xs="24" :md="6">
          <div class="mt-label">{{ t('manualTrade.volume') }}（{{ marketUnit }}）</div>
          <el-input-number v-model="form.volume" :min="100" :step="100" :max="100000" style="width:100%" />
        </el-col>
        <el-col :xs="24" :md="6">
          <div class="mt-label">{{ t('manualTrade.price') }}</div>
          <el-input-number v-model="form.price" :min="0.01" :precision="3" :step="0.01" style="width:100%" />
        </el-col>
        <el-col :xs="24" :md="6">
          <div class="mt-label">&nbsp;</div>
          <el-button type="primary" :loading="submitting" @click="submitOrder">{{ t('manualTrade.submit') }}</el-button>
          <el-button @click="resetForm">{{ t('manualTrade.reset') }}</el-button>
        </el-col>
      </el-row>

      <!-- 回执 -->
      <div v-if="receipt" class="mt-receipt">
        <el-tag :type="receipt.status === 'accepted' ? 'success' : receipt.status === 'rejected' ? 'danger' : 'warning'">
          {{ receipt.status }}
        </el-tag>
        <span class="mt-receipt-txt">req={{ receipt.req_id }}</span>
        <span v-if="receipt.vt_orderid" class="mt-receipt-txt">vt={{ receipt.vt_orderid }}</span>
        <span v-if="receipt.reason || receipt.error" class="mt-receipt-err">{{ receipt.reason || receipt.error }}</span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { cssVar } from '../utils/cssVar'
import { getTdInterfaces, stockDetail, stockSearch, getKline, stockIntraday,
         submitManualOrder, getManualOrderResult, apiErr } from '../api'

const { t } = useI18n()
use([CanvasRenderer, BarChart, LineChart, CandlestickChart,
     GridComponent, TooltipComponent, LegendComponent, MarkLineComponent])

const form = ref({ account_id: '', symbol: '', action: 'BUY', volume: 100, price: 0 })
const tdAccounts = ref([])
const symbolOptions = ref([])
const symbolLoading = ref(false)
const chartMode = ref('day')
const chartLoading = ref(false)
const klineData = ref([])
const intraday = ref({})
const quote = computed(() => detail.value.quote)
const detail = ref({})
const submitting = ref(false)
const receipt = ref(null)
let pollTimer = null
let chartSeq = 0

const intradayPoints = computed(() => intraday.value.points || [])
const marketUnit = computed(() => {
  const s = form.value.symbol || ''
  if (/^\d{6}\.(XSHG|XSHE)$/.test(s)) {
    const n = s.slice(0, 6)
    if (n.startsWith('11') || n.startsWith('12')) return t('manualTrade.unitLot')   // 可转债=张
    if (n.startsWith('5') || n.startsWith('1')) return t('manualTrade.unitShare')   // 场内基金=份
    return t('manualTrade.unitShare')                                              // A股=股
  }
  return t('manualTrade.unitDefault')
})

const fmtDepth = v => (v > 0 ? Number(v).toFixed(3).replace(/0+$/, '').replace(/\.$/, '') : '-')
const pickPrice = p => { if (p > 0) form.value.price = Number(p) }

onMounted(async () => {
  try {
    const d = await getTdInterfaces()
    tdAccounts.value = (d?.items || []).filter(x => x.enabled !== false)
    form.value.account_id = tdAccounts.value[0]?.code || ''
  } catch { /* 下拉空不阻断手输 */ }
})
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

const searchSymbols = async (q) => {
  if (!q || q.length < 2) { symbolOptions.value = []; return }
  symbolLoading.value = true
  try {
    const r = await stockSearch(q)
    symbolOptions.value = (r || []).map(x => x.symbol || x.ts_code || x).slice(0, 20)
  } catch { symbolOptions.value = [q] }
  finally { symbolLoading.value = false }
}

// 手输归一：6 位代码按交易所补后缀；9/6/68→XSHG，0/3→XSHE；已有 .SSE/.SZSE 归一到 vt
const normalizeSymbol = (raw) => {
  let s = String(raw || '').trim().toUpperCase()
  const dot = s.lastIndexOf('.')
  if (dot > 0) {
    const ex = s.slice(dot + 1)
    const map = { SSE: 'XSHG', SHSE: 'XSHG', XSHG: 'XSHG', SZSE: 'XSHE', XSHE: 'XSHE', BSE: 'BJSE' }
    s = s.slice(0, dot) + '.' + (map[ex] || ex)
  } else if (/^\d{6}$/.test(s)) {
    const n = s.slice(0, 1)
    s += (n === '9' || n === '6') ? '.XSHG' : '.XSHE'
  }
  return s
}

const onSymbolChange = async () => {
  form.value.symbol = normalizeSymbol(form.value.symbol)
  receipt.value = null
  if (!/^\d{6}\.[A-Z]{4}$/.test(form.value.symbol)) return
  const seq = ++chartSeq
  chartLoading.value = true
  try {
    const [d, k] = await Promise.all([stockDetail(form.value.symbol), getKline(form.value.symbol, 120)])
    if (seq !== chartSeq) return
    detail.value = d || {}
    klineData.value = k || []
    if (chartMode.value === 'intraday') await fetchIntraday(seq)
    const last = d?.quote?.last
    if (last > 0) form.value.price = Number(last)
  } catch { detail.value = {}; klineData.value = [] }
  finally { if (seq === chartSeq) chartLoading.value = false }
}

const fetchIntraday = async (seq = chartSeq) => {
  try {
    const d = await stockIntraday(form.value.symbol) || {}
    if (seq === chartSeq) intraday.value = d
  } catch { /* 分时失败保留旧数据 */ }
}
const onChartMode = async () => {
  if (chartMode.value === 'intraday') { chartLoading.value = true; await fetchIntraday(); chartLoading.value = false }
}

// 分时横轴：固定 09:30-11:30 + 13:00-15:00 两段拼接（4h=240 分钟），仅 1 天
const _intradayAxis = (() => {
  const axis = []
  for (let m = 9 * 60 + 30; m <= 11 * 60 + 30; m++) axis.push(`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`)
  for (let m = 13 * 60; m <= 15 * 60; m++) axis.push(`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`)
  return axis
})()
const _mapIdx = (hhmm) => {
  const [h, m] = String(hhmm || '').split(':').map(Number)
  if (isNaN(h) || isNaN(m)) return -1
  const v = h * 60 + m
  if (v >= 9 * 60 + 30 && v <= 11 * 60 + 30) return v - (9 * 60 + 30)
  if (v >= 13 * 60 && v <= 15 * 60) return 120 + v - 13 * 60
  return -1
}
const intradayOption = computed(() => {
  const pts = intradayPoints.value
  const up = cssVar('--up'), down = cssVar('--down')
  const first = pts[0]?.price ?? 0
  const n = _intradayAxis.length
  const price = new Array(n).fill(null), avg = new Array(n).fill(null), vol = new Array(n).fill(null)
  pts.forEach(p => {
    const i = _mapIdx(p.t)
    if (i >= 0 && i < n) { price[i] = p.price; avg[i] = p.avg; vol[i] = p.volume }
  })
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: [t('manualTrade.priceLine'), t('manualTrade.avgPrice')] },
    grid: [{ left: '8%', right: '3%', top: '8%', height: '54%' },
           { left: '8%', right: '3%', top: '70%', height: '18%' }],
    xAxis: [{ type: 'category', data: _intradayAxis, boundaryGap: false },
            { type: 'category', gridIndex: 1, data: _intradayAxis, axisLabel: { show: false }, boundaryGap: false }],
    yAxis: [{ type: 'value', scale: true }, { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
    series: [
      { name: t('manualTrade.priceLine'), type: 'line', data: price, showSymbol: false, connectNulls: false,
        lineStyle: { width: 1.5 }, areaStyle: { opacity: 0.06 },
        markLine: { symbol: 'none', silent: true, lineStyle: { color: cssVar('--text-secondary'), type: 'dashed' },
                    data: [{ yAxis: first }], label: { formatter: String(first) } } },
      { name: t('manualTrade.avgPrice'), type: 'line', data: avg, showSymbol: false, connectNulls: false,
        lineStyle: { width: 1, color: cssVar('--ma-line') } },
      { name: t('manualTrade.volume'), type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: vol.map((v, i) => ({ value: v, itemStyle: { color: (price[i] ?? 0) >= (avg[i] ?? 0) ? up : down } })) },
    ],
  }
})
const klineOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
  grid: [{ left: '8%', right: '3%', top: '8%', height: '56%' },
         { left: '8%', right: '3%', top: '72%', height: '18%' }],
  xAxis: [{ type: 'category', data: klineData.value.map(d => d.ts?.slice(0, 10)) },
          { type: 'category', gridIndex: 1, data: klineData.value.map(d => d.ts?.slice(0, 10)), axisLabel: { show: false } }],
  yAxis: [{ type: 'value', scale: true }, { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }],
  series: [
    { type: 'candlestick', data: klineData.value.map(d => [d.open, d.close, d.low, d.high]) },
    { type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
      data: klineData.value.map(d => ({ value: d.volume, itemStyle: { color: d.close >= d.open ? cssVar('--up') : cssVar('--down') } })) },
  ],
}))

const resetForm = () => {
  form.value = { account_id: tdAccounts.value[0]?.code || '', symbol: '', action: 'BUY', volume: 100, price: 0 }
  receipt.value = null; detail.value = {}; klineData.value = []; intraday.value = {}
}

const submitOrder = async () => {
  const f = form.value
  if (!f.account_id || !/^\d{6}\.[A-Z]{4}$/.test(f.symbol) || !(f.volume > 0) || !(f.price > 0)) {
    ElMessage.warning(t('manualTrade.invalidForm')); return
  }
  try {
    await ElMessageBox.confirm(
      `${f.account_id} | ${f.symbol} | ${f.action} | ${f.volume} | ${f.price}`,
      t('manualTrade.confirmTitle'), { confirmButtonText: t('manualTrade.confirmBtn') })
  } catch { return }
  submitting.value = true
  receipt.value = null
  try {
    const r = await submitManualOrder({ ...f, confirm: 'CONFIRM' })
    receipt.value = r
    if (r?.status === 'dispatched' && r?.req_id) startPoll(r.req_id)
    else ElMessage.success(t('manualTrade.submitted'))
  } catch (e) {
    receipt.value = { status: 'rejected', error: apiErr(e) }
  } finally { submitting.value = false }
}
const startPoll = (reqId) => {
  let n = 0
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    if (++n > 5) { clearInterval(pollTimer); pollTimer = null; return }
    try {
      const d = await getManualOrderResult(reqId)
      receipt.value = d
      if (d?.status === 'done') { clearInterval(pollTimer); pollTimer = null }
    } catch { /* 轮询失败保留 dispatched */ }
  }, 1000)
}
</script>

<style scoped>
.mt-page { padding: var(--sp-2); }
.mt-head { display: flex; justify-content: space-between; align-items: center; }
.mt-hint { color: var(--text-secondary); font-size: var(--fs-foot); }
.mt-row { margin-top: var(--sp-3); }
.mt-label { font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-1); }
.mt-unit { font-size: var(--fs-body); }
.mt-chart-row { margin-top: var(--sp-3); }
.mt-chart-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-1); }
.mt-chart-src { color: var(--text-secondary); font-size: var(--fs-foot); }
.mt-chart { height: 340px; }
.mt-depth { border: 1px solid var(--border); }
.mt-depth-row { display: flex; justify-content: space-between; padding: var(--sp-1); cursor: pointer; font-size: var(--fs-foot); }
.mt-depth-row:hover { background: var(--bg-hover); }
.mt-depth-row.ask b { color: var(--down); }
.mt-depth-row.bid b { color: var(--up); }
.mt-depth-mid { text-align: center; padding: var(--sp-1); font-size: var(--fs-body); font-weight: 600; border-block: 1px solid var(--border); }
.mt-depth-tip { color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-1); }
.mt-receipt { margin-top: var(--sp-3); display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; }
.mt-receipt-txt { font-size: var(--fs-foot); color: var(--text-secondary); }
.mt-receipt-err { font-size: var(--fs-foot); color: var(--danger); }
</style>
