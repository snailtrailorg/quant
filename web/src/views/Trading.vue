<template>
  <SellGuardBanner />
    <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('trading.title') }}</span>
        <el-button type="primary" @click="load">{{ t('common.refresh') }}</el-button>
      </div>
    </template>
    <el-alert v-if="loadFailed" type="error" :closable="false" show-icon
              :title="$t('trading.loadFailed')" style="margin-bottom: 12px" />
    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.totalAssets') }}</div>
    <el-alert v-if="dataSens" type="info" :closable="false" style="margin: 8px 0">
      {{ t('perm.sensLimited') }}: {{ dataSens }} — {{ positionData?.count ?? '—' }} {{ t('perm.sensCountUnit') }}
    </el-alert><div class="value">¥{{ formatNum(pnlData.total_value) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.todayPnl') }}</div><div class="value" :style="{color: (pnlData.today_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (pnlData.today_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ formatNum(pnlData.today_pnl) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.totalPnl') }}</div><div class="value" :style="{color: (pnlData.total_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (pnlData.total_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ formatNum(pnlData.total_pnl) }} ({{ pnlData.total_pnl_pct || 0 }}%)</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.positionCount') }}</div><div class="value">{{ positionData.positions?.length || 0 }}</div></div></el-card></el-col>
    </el-row>
    <el-tabs>
      <el-tab-pane :label="t('trading.positions')">
        <el-table :data="positionData.positions || []" size="small">
          <el-table-column prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" @click="gotoDetail(row.symbol)">{{ (row.symbol||'').split('.')[0] }}</el-link>
            </template>
          </el-table-column>
          <!-- P2-11（05 §5.2/06 B#4）：API 已有字段全展示——direction/frozen/cost/pnl（现状只 2 列） -->
          <el-table-column prop="direction" :label="t('trading.dirCol')" width="70">
            <template #default="{ row }"><el-tag size="small" :type="row.direction === 'short' ? 'danger' : 'primary'">{{ row.direction === 'short' ? '空' : '多' }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="volume" :label="t('trading.volume')" width="80" class-name="num" />
          <el-table-column prop="frozen" :label="t('trading.frozenCol')" width="70" class-name="num" />
          <el-table-column prop="cost_price" :label="t('trading.costCol')" width="90" class-name="num" />
          <el-table-column :label="t('trading.lastPrice')" width="75" class-name="num">
            <template #default="{ row }">{{ lastPrices[row.symbol?.split('.')[0]] || '—' }}</template>
          </el-table-column>
          <el-table-column :label="t('trading.pnlCol')" width="110" class-name="num">
            <template #default="{ row }">
              <span v-if="row.pnl != null" :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl >= 0 ? '▲' : '▼' }} {{ row.pnl.toFixed(0) }}</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('trading.pnlPct')" width="70" class-name="num">
            <template #default="{ row }">
              <span v-if="row.cost_price > 0 && row.pnl != null" :class="row.pnl >= 0 ? 'up' : 'down'">
                {{ (row.pnl / (row.cost_price * row.volume) * 100).toFixed(1) }}%
              </span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('trading.mktValue')" width="90" class-name="num">
            <template #default="{ row }">{{ row.cost_price && row.volume ? fmtCn(row.cost_price * row.volume, 1) : '—' }}</template>
          </el-table-column>
          <el-table-column :label="t('common.action')" width="90">
            <template #default="{ row }">
              <el-button type="primary" size="small" @click="gotoDetail(row.symbol)">{{ t('common.detail') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: 6px; display: flex; justify-content: space-between">
          <span>{{ t('trading.snapshotNote') }}{{ positionData.snapshot_ts ? positionData.snapshot_ts.slice(0, 19) : '—' }}</span>
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
      <!-- 05 §5.2 要点 8:人工单登记(底仓/场外手动单回流对账豁免基准) -->
      <el-tab-pane :label="t('trading.manualOrders')">
        <el-form inline>
          <el-form-item label="Symbol"><el-input v-model="manualForm.symbol" placeholder="600000" style="width: 100px" /></el-form-item>
          <el-form-item :label="t('trading.direction')">
            <el-select v-model="manualForm.action" style="width: 80px">
              <el-option value="BUY" :label="t('dashboard.buy')" /><el-option value="SELL" :label="t('dashboard.sell')" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('trading.volume')"><el-input-number v-model="manualForm.volume" :step="100" style="width: 100px" /></el-form-item>
          <el-form-item :label="t('trading.price')"><el-input-number v-model="manualForm.price" :step="0.01" :precision="2" style="width: 90px" /></el-form-item>
          <el-form-item><el-button type="primary" @click="submitManual">{{ t('common.confirm') }}</el-button></el-form-item>
        </el-form>
        <div style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('trading.manualHint') }}</div>
      </el-tab-pane>
      <el-tab-pane :label="t('trading.orders')">
        <el-table :data="ordersData.orders || []" size="small">
          <el-table-column prop="ts" :label="t('trading.time')" width="150" />
          <el-table-column prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip />
          <el-table-column prop="action" :label="t('trading.direction')" width="80">
            <template #default="{ row }">
              <!-- BUY=买入红(A股习惯)/SELL=卖出绿;中文化 05 §5.2 要点 3 -->
              <el-tag size="small" :type="row.action === 'BUY' ? 'danger' : 'success'">{{ row.action === 'BUY' ? t('dashboard.buy') : t('dashboard.sell') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="volume" :label="t('trading.volume')" width="80" class-name="num" />
          <el-table-column prop="price" :label="t('trading.price')" width="90" class-name="num" />
          <el-table-column prop="status" :label="t('common.status')" width="90" />
          <el-table-column prop="error" :label="t('backtest.reason')" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="t('trading.pnl')">
        <div v-if="pnlData.curve?.length" style="height: 400px">
          <v-chart :option="pnlChartOption" autoresize />
        </div>
        <div v-else style="height: 400px; display: flex; align-items: center; justify-content: center; color: #999">
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
const pnlData = ref({})
const pnlChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: '5%', right: '5%', bottom: '5%', containLabel: true },
  xAxis: { type: 'category', data: (pnlData.value.curve || []).map(c => c.ts?.slice(0, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ name: t('trading.equity'), type: 'line', data: (pnlData.value.curve || []).map(c => c.value), smooth: true }],
}))
const formatNum = (n) => (n || 0).toFixed(0)
import { fmtCn } from '../utils/format'
const loadFailed = ref(false)
const dataSens = ref('')
const lastUpdate = ref('—')
const load = async () => {
  // P2（审计 C3）：静默空表=交易系统假空显示
  loadFailed.value = false
  try {
    positionData.value = await getPosition()
    ordersData.value = await getOrders()
    // W5：脱敏态(count/aggregated)不再渲染空表误读为无持仓——提示条替代
    const sens = positionData.value?.sensitivity || 'detail'
    if (sens !== 'detail') dataSens.value = sens
  } catch { loadFailed.value = true }
  try { pnlData.value = await getPnl() } catch { }
  lastUpdate.value = new Date().toLocaleTimeString()
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
const manualForm = ref({ symbol: '', action: 'BUY', volume: 0, price: 0 })
const submitManual = async () => {
  try { await api.post('/reconcile/manual-order', { ...manualForm.value, note: '交易台人工单登记' }); ElMessage.success(t('common.success')) }
  catch { ElMessage.error(t('common.failed')) }
}
import api from '../api'
import { ElMessage } from 'element-plus'
import { onUnmounted } from 'vue'
let pollTimer = null
onMounted(() => {
  load()
  pollTimer = setInterval(load, isTradingHours() ? 5000 : 60000)
})
onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>
