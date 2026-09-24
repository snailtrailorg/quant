<template>
  <SellGuardBanner />
    <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('trading.title') }}</span>
        <RefreshBtn @refresh="load" />
      </div>
    </template>
    <el-alert v-if="loadFailed" type="error" :closable="false" show-icon
              :title="$t('trading.loadFailed')" style="margin-bottom: 12px" />
    <!-- wd-20 §2.4：KpiCard 四卡 + flex 首屏（与 Dashboard 同构；pnl 色走 up/down 令牌类） -->
    <div style="display: flex; gap: var(--sp-4); flex-wrap: wrap; margin-bottom: var(--sp-5)">
      <KpiCard :label="t('trading.totalAssets')" :value="'¥' + formatNum(positionData.total_value)" />
      <KpiCard :label="t('trading.todayPnl')" :value="(pnlData.today_pnl||0) >= 0 ? '▲¥' + formatNum(pnlData.today_pnl) : '▼¥' + formatNum(pnlData.today_pnl)"
               :tone="(pnlData.today_pnl||0) >= 0 ? 'up' : 'down'" />
      <KpiCard :label="t('trading.totalPnl')" :value="(pnlData.total_pnl||0) >= 0 ? '▲¥' + formatNum(pnlData.total_pnl) : '▼¥' + formatNum(pnlData.total_pnl)"
               :tone="(pnlData.total_pnl||0) >= 0 ? 'up' : 'down'"
               :sub="pnlData.total_pnl_pct ? (pnlData.total_pnl_pct + '%') : ''" />
      <KpiCard :label="t('trading.positionCount')" :value="positions.length || 0" />
    </div>
    <el-tabs>
      <el-tab-pane :label="t('trading.positions')">
        <!-- 批17 17A：列宽拖拽+持久化 -->
        <TableShell :data="positions" size="small" storage-key="trading-positions">
          <el-table-column prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" @click="gotoDetail(row.symbol)">{{ (row.symbol||'').split('.')[0] }}</el-link>
            </template>
          </el-table-column>
          <!-- P2-11（05 §5.2/06 B#4）：API 已有字段全展示——direction/frozen/cost/pnl（现状只 2 列） -->
          <el-table-column prop="direction" :label="t('trading.dirCol')" min-width="80">
            <template #default="{ row }"><el-tag size="small" :type="row.direction === 'direction_short' ? 'danger' : 'primary'">{{ row.direction === 'direction_short' ? t('trading.shortTag') : t('trading.longTag') }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="volume" :label="t('trading.volume')" min-width="80" class-name="num" />
          <el-table-column prop="frozen" :label="t('trading.frozenCol')" min-width="80" class-name="num" />
          <el-table-column prop="cost_price" :label="t('trading.costCol')" min-width="90" class-name="num" />
          <el-table-column prop="last_price" :label="t('trading.lastPrice')" min-width="80" class-name="num">
            <template #default="{ row }">{{ lastPrices[row.symbol?.split('.')[0]] || '—' }}</template>
          </el-table-column>
          <el-table-column prop="pnl" :label="t('trading.pnlCol')" min-width="110" class-name="num">
            <template #default="{ row }">
              <span v-if="row.pnl != null" :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl >= 0 ? '▲' : '▼' }} {{ row.pnl.toFixed(0) }}</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column prop="pnl_pct" :label="t('trading.pnlPct')" min-width="80" class-name="num">
            <template #default="{ row }">
              <span v-if="row.cost_price > 0 && row.pnl != null" :class="row.pnl >= 0 ? 'up' : 'down'">
                {{ (row.pnl / (row.cost_price * row.volume) * 100).toFixed(1) }}%
              </span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column prop="market_value" :label="t('trading.mktValue')" min-width="100" class-name="num">
            <template #default="{ row }">{{ row.cost_price && row.volume ? fmtCn(row.cost_price * row.volume, 1) : '—' }}</template>
          </el-table-column>
          <el-table-column prop="actions" :label="t('common.action')" min-width="90">
            <template #default="{ row }">
              <IconBtn size="small" :icon="View" :title="t('common.detail')" @click="gotoDetail(row.symbol)" />
            </template>
          </el-table-column>
        </TableShell>
        <!-- wd-20 §1.6：stale 黄条——停更防被读成空仓（N-S5 语义：停更≠空仓） -->
        <el-alert v-if="positionData.stale" type="warning" :closable="false" style="margin: var(--sp-2) 0">
          <template #title>
            {{ t('trading.staleWarn') }}
            <el-tooltip v-if="snapshotAccount.snapshot_rows != null" :content="t('trading.snapshotRowsTip', { n: snapshotAccount.snapshot_rows })">
              <el-icon style="vertical-align: middle"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-alert>
        <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: 6px; display: flex; justify-content: space-between">
          <span>{{ t('trading.snapshotNote') }}{{ snapshotAccount.snapshot_ts ? fmtTime.full(snapshotAccount.snapshot_ts) : '—' }}</span>
          <span>{{ t('trading.lastUpdate') }}: {{ lastUpdate }}</span>
        </div>
      </el-tab-pane>
      <!-- 05 §5.2 要点 5:盘后自动展示当日成交汇总 -->
      <el-tab-pane :label="t('trading.dailySummary')">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item :label="t('trading.totalTrades')">{{ todayOrders.length }}</el-descriptions-item>
          <el-descriptions-item :label="t('trading.buyCount')">{{ todayOrders.filter(o => o.action === 'BUY').length }}</el-descriptions-item>
          <el-descriptions-item :label="t('trading.sellCount')">{{ todayOrders.filter(o => o.action === 'SELL').length }}</el-descriptions-item>
          <el-descriptions-item :label="t('trading.totalVolume')">{{ todayOrders.reduce((s, o) => s + (o.volume || 0), 0) }}</el-descriptions-item>
          <el-descriptions-item :label="t('trading.buyAmount')">{{ fmtCn(todayOrders.filter(o => o.action === 'BUY').reduce((s, o) => s + (o.price || 0) * (o.volume || 0), 0), 1) }}</el-descriptions-item>
          <el-descriptions-item :label="t('trading.sellAmount')">{{ fmtCn(todayOrders.filter(o => o.action === 'SELL').reduce((s, o) => s + (o.price || 0) * (o.volume || 0), 0), 1) }}</el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>
      <!-- 批36a-2：人工单登记 tab 退役——与风控·三账对账·场外单登记同端点同语义
           （POST /reconcile/manual-order），且本页无 user_mgmt 门（Trader 填单 403 静默）
           +direction/price 是死字段（端点只收 symbol/volume）。统一走 Reconcile 一处。 -->
      <el-tab-pane :label="t('trading.orders')">
        <div style="display: flex; justify-content: flex-end; margin-bottom: var(--sp-2)">
          <ColumnSettings storage-key="cols.trading-orders" :columns="orderColDefs" v-model:visible="orderVisible" />
        </div>
        <!-- 批17 17A：列宽拖拽+持久化；17B 列显隐（时间类低频列默认隐） -->
        <TableShell :data="ordersData.orders || []" size="small" storage-key="trading-orders">
          <el-table-column v-if="orderOn('ts')" prop="ts" :label="t('trading.time')" width="220" />
          <el-table-column prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip />
          <el-table-column v-if="orderOn('action')" prop="action" :label="t('trading.direction')" min-width="80">
            <template #default="{ row }">
              <!-- BUY=买入红(A股习惯)/SELL=卖出绿;中文化 05 §5.2 要点 3 -->
              <el-tag size="small" :type="row.action === 'BUY' ? 'danger' : 'success'">{{ row.action === 'BUY' ? t('dashboard.buy') : t('dashboard.sell') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="orderOn('volume')" prop="volume" :label="t('trading.volume')" min-width="80" class-name="num" />
          <el-table-column v-if="orderOn('price')" prop="price" :label="t('trading.price')" min-width="90" class-name="num" />
          <el-table-column v-if="orderOn('status')" prop="status" :label="t('common.status')" min-width="90" />
          <el-table-column v-if="orderOn('client_order_id')" prop="client_order_id" :label="t('trading.orderRefCol')" min-width="130" class-name="num" show-overflow-tooltip />
          <!-- 批16：+策略列（order_log.strategy_id 真实归属；持仓表不加——数据模型级缺失，盲审 B-P0） -->
          <el-table-column v-if="orderOn('strategy_id')" prop="strategy_id" min-width="130" show-overflow-tooltip>
            <template #header>
              <span :title="t('trading.strategyColTip')">{{ t('cols.strategy') }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="orderOn('error')" prop="error" :label="t('backtest.reason')" show-overflow-tooltip />
        </TableShell>
      </el-tab-pane>
      <el-tab-pane :label="t('trading.pnl')">
        <div v-if="pnlCurve.length" style="height: 400px">
          <v-chart :option="pnlChartOption" autoresize />
        </div>
        <div v-else style="height: 400px; display: flex; align-items: center; justify-content: center; color: var(--text-secondary)">
          {{ t('trading.noPnlHint') }}
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { fmtTime } from '../utils/fmtTime'
import SellGuardBanner from '../components/SellGuardBanner.vue'
import { getPosition, getOrders, getPnl } from '../api'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'

const router = useRouter()
const gotoDetail = symbol => router.push(`/stock/${symbol}`)
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

const { t } = useI18n()
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const positionData = ref({})
const ordersData = ref({})
// D2：get_position 按 account 分组返回——粗显聚合各 account 持仓（细显留后续）
const positions = computed(() => (positionData.value?.accounts || []).flatMap(v => v.positions || []))

// 批17 17B：委托表列显隐（方案圈定——时间类低频列默认隐；全列可配，无锁定列）
const orderColDefs = computed(() => [
  { key: 'ts', label: t('trading.time') },   // 盲审A-P2-9：委托表唯一时序锚，不默认隐
  { key: 'action', label: t('trading.direction') },
  { key: 'volume', label: t('trading.volume') },
  { key: 'price', label: t('trading.price') },
  { key: 'status', label: t('common.status') },
  { key: 'client_order_id', label: t('trading.orderRefCol') },
  { key: 'strategy_id', label: t('cols.strategy') },
  { key: 'error', label: t('backtest.reason') },
])
const orderVisible = ref([])
const orderOn = k => orderVisible.value.includes(k)
const pnlData = ref({})
// D2：get_pnl 按 account 分组返回——粗显取首个 account 曲线（细显留后续）
const pnlCurve = computed(() => (pnlData.value?.accounts || [])[0]?.curve || [])
// D2：snapshot 时间/行数已下沉到 account 对象——粗显取首个 account（细显留后续）
const snapshotAccount = computed(() => (positionData.value?.accounts || [])[0] || {})
const lastPrices = ref({})   // wd-20 §1.4.2：现价恢复（行情快照联动）
const pnlChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: pnlCurve.value.map(c => fmtTime.day(c.ts)) },
  yAxis: { type: 'value', scale: true },
  series: [{ name: t('trading.equity'), type: 'line', data: pnlCurve.value.map(c => c.value), smooth: true }],
}))
const formatNum = (n) => (n || 0).toFixed(0)
import { fmtCn } from '../utils/format'
import { stockDetail } from '../api'
import KpiCard from '../components/KpiCard.vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import RefreshBtn from '../components/RefreshBtn.vue'
import IconBtn from '../components/IconBtn.vue'
import { QuestionFilled, View } from '@element-plus/icons-vue'
const loadFailed = ref(false)
const lastUpdate = ref('—')
const load = async () => {
  // P2（审计 C3）：静默空表=交易系统假空显示
  loadFailed.value = false
  try {
    positionData.value = await getPosition()
    ordersData.value = await getOrders()
  } catch { loadFailed.value = true }
  try { pnlData.value = await getPnl() } catch { }
  lastUpdate.value = new Date().toLocaleTimeString()
  loadPrices()   // wd-20 §1.4.2：现价随 5s/60s 轮询联动（不阻塞主 load）
}
const loadPrices = async () => {
  // 盲审 P2：filter 掉空 symbol + 去重（原 map 直接对 undefined/重复标的发请求）
  const symbols = [...new Set(positions.value.map(p => p.symbol).filter(Boolean))]
  await Promise.all(symbols.map(async symbol => {
    try {
      const d = await stockDetail(symbol)   // 带后缀（detail 端点 to_vt_symbol 不推断交易所，去后缀=404）
      const q = d?.quote
      if (q?.last != null) lastPrices.value[symbol.split('.')[0]] = q.last
    } catch { /* 单标的行情失败不阻塞其余 */ }
  }))
}
// 05 §5.2 要点 2:现价/浮盈由行情快照联动,5s 轮询(盘中)/60s(盘后)
const isTradingHours = () => {
  const now = new Date(); const hm = now.getHours() * 100 + now.getMinutes(); const dw = now.getDay()
  return dw >= 1 && dw <= 5 && ((hm >= 930 && hm < 1130) || (hm >= 1300 && hm < 1500))
}
const todayOrders = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  return (ordersData.value?.orders || []).filter(o => (o.ts || '').startsWith(today))
})
import { onUnmounted } from 'vue'
let pollTimer = null
onMounted(() => {
  load()
  pollTimer = setInterval(load, isTradingHours() ? 5000 : 60000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
</style>
