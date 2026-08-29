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
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.totalAssets') }}</div><div class="value">¥{{ formatNum(pnlData.total_value) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.todayPnl') }}</div><div class="value" :style="{color: (pnlData.today_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (pnlData.today_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ formatNum(pnlData.today_pnl) }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.totalPnl') }}</div><div class="value" :style="{color: (pnlData.total_pnl||0) >= 0 ? '#C8102E' : '#0A7A54'}">{{ (pnlData.total_pnl||0) >= 0 ? '▲' : '▼' }}¥{{ formatNum(pnlData.total_pnl) }} ({{ pnlData.total_pnl_pct || 0 }}%)"</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('trading.positionCount') }}</div><div class="value">{{ positionData.positions?.length || 0 }}</div></div></el-card></el-col>
    </el-row>
    <el-tabs>
      <el-tab-pane :label="t('trading.positions')">
        <el-table :data="positionData.positions || []" stripe size="small">
          <el-table-column prop="symbol" :label="t('common.symbol')" width="110">
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
          <el-table-column :label="t('trading.pnlCol')" width="110" class-name="num">
            <template #default="{ row }">
              <span v-if="row.pnl != null" :class="row.pnl >= 0 ? 'up' : 'down'">{{ row.pnl >= 0 ? '▲' : '▼' }} {{ row.pnl.toFixed(0) }}</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.action')" width="90">
            <template #default="{ row }">
              <el-button type="primary" size="small" @click="gotoDetail(row.symbol)">{{ t('common.detail') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: 6px">
          {{ t('trading.snapshotNote') }}{{ positionData.snapshot_ts ? positionData.snapshot_ts.slice(0, 19) : '—' }}
        </div>
      </el-tab-pane>
      <el-tab-pane :label="t('trading.orders')">
        <el-table :data="ordersData.orders || []" stripe size="small">
          <el-table-column prop="ts" :label="t('trading.time')" width="150" />
          <el-table-column prop="symbol" :label="t('common.symbol')" width="110" />
          <el-table-column prop="action" :label="t('trading.direction')" width="80">
            <template #default="{ row }">
              <!-- BUY=买入红(A股习惯)/SELL=卖出绿;中文化 05 §5.2 要点 3 -->
              <el-tag size="small" :type="row.action === 'BUY' ? 'danger' : 'success'">{{ row.action === 'BUY' ? t('dash.buy') : t('dash.sell') }}</el-tag>
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
const loadFailed = ref(false)
const load = async () => {
  // P2（审计 C3）：静默空表=交易系统假空显示
  loadFailed.value = false
  try { positionData.value = await getPosition() } catch { loadFailed.value = true }
  try { ordersData.value = await getOrders() } catch { }
  try { pnlData.value = await getPnl() } catch { }
}
onMounted(load)
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>
