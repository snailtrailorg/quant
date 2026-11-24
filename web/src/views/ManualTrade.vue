<template>
  <div class="mt-page">
  <ContentCard :title="t('manualTrade.cardTitle')" class="mt-form-card">
    <template #actions>
      <IconBtn :icon="Refresh" :title="t('manualTrade.refresh')" @click="reload" />
    </template>

    <!-- 顶部单行工具条：接口/标的/方向/数量/价格/下单/重置 -->
    <el-form ref="formRef" :model="form" :rules="rules" inline class="mt-toolbar" @submit.prevent>
      <el-form-item :label="t('manualTrade.account')" prop="account_id" class="mt-f-account">
        <el-select v-model="form.account_id" :placeholder="t('manualTrade.accountPh')">
          <el-option v-for="a in tdAccounts" :key="a.code" :label="a.code" :value="a.code" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('manualTrade.symbol')" prop="symbol" class="mt-f-symbol">
        <el-select v-model="form.symbol" filterable remote :remote-method="searchSymbols"
          :loading="symbolLoading" :placeholder="t('manualTrade.symbolPh')"
          @change="onSymbolChange" allow-create>
          <el-option v-for="s in symbolOptions" :key="s" :label="s" :value="s" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('manualTrade.action')" class="mt-f-dir">
        <el-radio-group v-model="form.action">
          <el-radio-button value="BUY">{{ t('dashboard.buy') }}</el-radio-button>
          <el-radio-button value="SELL">{{ t('dashboard.sell') }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="`${t('manualTrade.volume')}（${marketUnit}）`" prop="volume" class="mt-f-vol">
        <el-input-number v-model="form.volume" :min="100" :step="100" :max="100000" />
      </el-form-item>
      <el-form-item :label="t('manualTrade.price')" prop="price" class="mt-f-price">
        <el-input-number v-model="form.price" :min="0.01" :precision="tickDecimals" :step="0.01" />
      </el-form-item>
      <el-form-item class="mt-f-btns">
        <el-button type="primary" :loading="submitting" class="mt-btn" @click="submitOrder">{{ t('manualTrade.submit') }}</el-button>
        <el-button class="mt-btn" @click="resetForm">{{ t('manualTrade.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </ContentCard>

  <!-- 行情/盘口两张卡，等高 -->
  <div class="mt-main">
    <ContentCard :title="chartTitle" class="mt-chart-card">
      <div class="mt-chart-head">
        <el-radio-group v-model="chartMode" size="small" @change="onChartMode">
          <el-radio-button value="day">{{ t('manualTrade.dayK') }}</el-radio-button>
          <el-radio-button value="intraday">{{ t('manualTrade.intraday') }}</el-radio-button>
        </el-radio-group>
      </div>
      <div v-loading="chartLoading" class="mt-chart">
        <v-chart v-if="chartMode==='day' && klineData.length" :option="klineOption" autoresize class="mt-chart-canvas" />
        <v-chart v-else-if="chartMode==='intraday' && intradayPoints.length" :option="intradayOption" autoresize class="mt-chart-canvas" />
        <EmptyState v-else :description="t('manualTrade.noChart')" size="small" />
      </div>
    </ContentCard>

    <ContentCard :title="depthTitle" class="mt-depth-card">
      <div class="mt-depth-body">
        <table v-if="quote?.bid?.length" class="mt-depth">
          <thead>
            <tr>
              <th>{{ t('manualTrade.level') }}</th>
              <th class="num">{{ t('manualTrade.price') }}</th>
              <th class="num">{{ t('manualTrade.qty') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="lv in asks" :key="lv.label" :class="['ask', { off: !(lv.price > 0) }]"
              role="button" tabindex="0" @click="pickPrice(lv.price)" @keydown.enter="pickPrice(lv.price)">
              <td>{{ lv.label }}</td>
              <td class="num">{{ fmtPrice(lv.price) }}</td>
              <td class="num">{{ fmtQty(lv.vol) }}</td>
            </tr>
            <tr v-for="(lv, idx) in bids" :key="lv.label" :class="['bid', { off: !(lv.price > 0), first: idx === 0 }]"
              role="button" tabindex="0" @click="pickPrice(lv.price)" @keydown.enter="pickPrice(lv.price)">
              <td>{{ lv.label }}</td>
              <td class="num">{{ fmtPrice(lv.price) }}</td>
              <td class="num">{{ fmtQty(lv.vol) }}</td>
            </tr>
          </tbody>
        </table>
        <EmptyState v-else :description="t('manualTrade.noDepth')" size="small" />
      </div>
    </ContentCard>
  </div>

    <!-- 下方仅状态行 -->
    <div class="mt-status">
      <template v-if="receipt">
        <StatusTag :value="receipt.status || 'pending'" />
        <span v-if="receipt.req_id">{{ t('manualTrade.reqId') }} {{ receipt.req_id }}</span>
        <span v-if="receipt.vt_orderid">{{ t('manualTrade.vtId') }} {{ receipt.vt_orderid }}</span>
        <span v-if="receipt.volume">{{ receipt.volume }} @ {{ fmtPrice(receipt.price) }}</span>
        <span v-if="receipt.reason || receipt.error" class="mt-err">{{ receipt.reason || receipt.error }}</span>
      </template>
      <span v-else class="mt-muted">—</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, LineChart, CandlestickChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, MarkLineComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import ContentCard from '../components/ContentCard.vue'
import StatusTag from '../components/StatusTag.vue'
import EmptyState from '../components/EmptyState.vue'
import IconBtn from '../components/IconBtn.vue'
import { cssVar } from '../utils/cssVar'
import { getTdInterfaces, stockDetail, stockSearch, getKline, stockIntraday,
         submitManualOrder, getManualOrderResult, apiErr } from '../api'

const { t } = useI18n()
use([CanvasRenderer, BarChart, LineChart, CandlestickChart,
     GridComponent, TooltipComponent, LegendComponent, MarkLineComponent])

const formRef = ref(null)
const form = ref({ account_id: '', symbol: '', action: 'BUY', volume: 100, price: 0 })
const rules = computed(() => ({
  account_id: [{ required: true, message: t('manualTrade.invalidForm'), trigger: 'change' }],
  symbol: [{ required: true, pattern: /^\d{6}\.[A-Z]{4}$/, message: t('manualTrade.invalidForm'), trigger: 'change' }],
  volume: [{ required: true, message: t('manualTrade.invalidForm'), trigger: 'change' }],
  price: [{ required: true, message: t('manualTrade.invalidForm'), trigger: 'change' }],
}))
const tdAccounts = ref([])
const symbolOptions = ref([])
const symbolLoading = ref(false)
const chartMode = ref('day')
const chartLoading = ref(false)
const klineData = ref([])
const intraday = ref({})
const detail = ref({})
const quote = computed(() => detail.value.quote)
const submitting = ref(false)
const receipt = ref(null)
let pollTimer = null
let chartSeq = 0

const intradayPoints = computed(() => intraday.value.points || [])
const marketUnit = computed(() => {
  const s = form.value.symbol || ''
  if (/^\d{6}\.(XSHG|XSHE)$/.test(s)) {
    const n = s.slice(0, 6)
    if (n.startsWith('11') || n.startsWith('12')) return t('manualTrade.unitLot')
    return t('manualTrade.unitShare')
  }
  return t('manualTrade.unitDefault')
})
const tickDecimals = computed(() => {
  const ts = Number(detail.value?.tick_size || detail.value?.instrument?.tick_size || 0.01)
  if (!(ts > 0)) return 2
  return Math.max(0, Math.min(4, Math.round(-Math.log10(ts))))
})
const fmtPrice = (v) => (v > 0 ? Number(v).toFixed(tickDecimals.value) : '-')
const fmtQty = (v) => (v == null || v === '' ? '-' : Number(v).toLocaleString())
const chartTitle = computed(() => detail.value?.name || quote.value?.name || form.value.symbol || t('manualTrade.chartTitle'))
const depthTitle = computed(() => (quote.value?.last > 0 ? fmtPrice(quote.value.last) : t('manualTrade.depth')))
const asks = computed(() => {
  const q = quote.value || {}
  const out = []
  for (let i = 1; i <= 5; i++) {
    out.push({ label: `${t('manualTrade.ask')}${6 - i}`, price: q.ask?.[5 - i], vol: q.ask_v?.[5 - i] })
  }
  return out
})
const bids = computed(() => {
  const q = quote.value || {}
  const out = []
  for (let i = 1; i <= 5; i++) {
    out.push({ label: `${t('manualTrade.bid')}${i}`, price: q.bid?.[i - 1], vol: q.bid_v?.[i - 1] })
  }
  return out
})
const pickPrice = (p) => { if (p > 0) form.value.price = Number(p) }

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

const normalizeSymbol = (raw) => {
  let s = String(raw || '').trim().toUpperCase()
  const dot = s.lastIndexOf('.')
  if (dot > 0) {
    const ex = s.slice(dot + 1)
    const map = { SSE: 'XSHG', SHSE: 'XSHG', XSHG: 'XSHG', SZSE: 'XSHE', XSHE: 'XSHE', BSE: 'BJSE' }
    s = s.slice(0, dot) + '.' + (map[ex] || ex)
  } else if (/^\d{6}$/.test(s)) {
    s += (s.startsWith('9') || s.startsWith('6')) ? '.XSHG' : '.XSHE'
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
  } catch { /* 保留旧数据 */ }
}
const onChartMode = async () => {
  if (chartMode.value === 'intraday') { chartLoading.value = true; await fetchIntraday(); chartLoading.value = false }
}
const reload = () => { if (form.value.symbol) onSymbolChange() }

// 分时横轴：固定 09:30-11:30 + 13:00-15:00（两段拼接，仅 1 天）
const _intradayAxis = (() => {
  const axis = []
  const pad = (n) => String(n).padStart(2, '0')
  for (let m = 9 * 60 + 30; m <= 11 * 60 + 30; m++) axis.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`)
  for (let m = 13 * 60; m <= 15 * 60; m++) axis.push(`${pad(Math.floor(m / 60))}:${pad(m % 60)}`)
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
    grid: [{ left: '8%', right: '3%', top: '10%', height: '52%' },
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
  formRef.value?.clearValidate()
}

const submitOrder = async () => {
  try { await formRef.value?.validate() } catch { return }
  const f = form.value
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
    } catch { /* 保留 dispatched */ }
  }, 1000)
}
</script>

<style scoped>
.mt-page { display: flex; flex-direction: column; gap: var(--sp-3); }
.mt-page :deep(.el-card__header) { font-size: var(--fs-card); }
.mt-page :deep(.el-card__header span) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-form-card { flex: 0 0 auto; }
.mt-toolbar { display: flex; flex-wrap: nowrap; align-items: flex-start; gap: var(--sp-2); }
.mt-toolbar :deep(.el-form-item) { margin: 0; min-width: 0; }
.mt-toolbar :deep(.el-form-item__content) { flex: 1 1 auto; min-width: 0; }
.mt-toolbar :deep(.el-select), .mt-toolbar :deep(.el-input-number) { width: 100%; }
.mt-f-account { flex: 112 1 0; }
.mt-f-symbol { flex: 168 1 0; }
.mt-f-dir { flex: 104 1 0; }
.mt-f-dir :deep(.el-radio-group) { display: flex; width: 100%; }
.mt-f-dir :deep(.el-radio-button) { flex: 1 1 0; }
.mt-f-dir :deep(.el-radio-button__inner) { width: 100%; }
.mt-f-vol { flex: 120 1 0; }
.mt-f-price { flex: 120 1 0; }
.mt-f-btns { flex: 0 0 128px; }
.mt-f-btns :deep(.el-form-item__content) { gap: var(--sp-1); flex-wrap: nowrap; }
.mt-btn { width: 60px; }
.mt-main { display: flex; gap: var(--sp-3); height: 420px; align-items: stretch; }
.mt-chart-card { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.mt-depth-card { flex: 0 0 240px; min-width: 0; display: flex; flex-direction: column; }
.mt-chart-card :deep(.el-card__body),
.mt-depth-card :deep(.el-card__body) { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.mt-chart-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-1); }
.mt-chart { flex: 1; min-height: 0; }
.mt-chart-canvas { width: 100%; height: 100%; }
.mt-depth-body { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; }
.mt-depth { width: 100%; border-collapse: collapse; font-size: var(--fs-body); }
.mt-depth th { padding: var(--sp-1) 0; color: var(--text-secondary); font-size: var(--fs-label); font-weight: 400; text-align: right; border-bottom: 1px solid var(--border); }
.mt-depth th:first-child, .mt-depth td:first-child { text-align: left; }
.mt-depth td { padding: var(--sp-1) 0; text-align: right; }
.mt-depth .num { font-family: var(--font-num); font-variant-numeric: tabular-nums; }
.mt-depth tbody tr { cursor: pointer; }
.mt-depth tbody tr:hover { background: var(--bg-hover); }
.mt-depth .ask td:nth-child(2) { color: var(--down); }
.mt-depth .bid td:nth-child(2) { color: var(--up); }
.mt-depth tr.off { color: var(--text-secondary); cursor: default; }
.mt-depth tr.bid.first td { border-top: 1px solid var(--border); }
.mt-muted { color: var(--text-secondary); font-size: var(--fs-foot); }
.mt-status { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; font-size: var(--fs-foot); color: var(--text-secondary); }
.mt-err { color: var(--danger); }
@media (max-width: 1400px) { .mt-depth-card { flex-basis: 200px; } }
@media (max-width: 1279px) { .mt-toolbar { flex-wrap: wrap; } .mt-f-btns { flex: 0 0 auto; } }
</style>
