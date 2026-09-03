<template>
  <el-card v-loading="loading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('backtest.detailTitle', { id: $route.params.id }) }}</span>
        <el-button type="primary" @click="$router.back()">{{ t('common.return') }}</el-button>
        <el-button type="success" @click="markVerified" :disabled="!run.strategy_config_id">{{ t('backtest.markVerified') }}</el-button>
        <!-- P2-5（05 §5.7 要点 4）：三级开关终点——以此结果创建实盘任务（预填策略/参数） -->
        <el-button type="primary" @click="createLiveFromRun" :disabled="!run.strategy_config_id">{{ t('backtest.createLive') }}</el-button>
      </div>
    </template>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.totalReturn') }}</div><div class="value">{{ run.total_return_pct ?? '-' }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.winRate') }}</div><div class="value">{{ run.win_rate ?? '-' }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.sharpe') }}</div><div class="value">{{ run.sharpe_ratio ?? '-' }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.maxDrawdown') }}</div><div class="value">{{ run.max_drawdown_pct ?? '-' }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.tradeCount') }}</div><div class="value">{{ run.trade_count ?? '—' }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.annualized') }}</div><div class="value">{{ run.annualized_return != null ? (run.annualized_return).toFixed(1) + '%' : '—' }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">{{ t('backtest.plRatio') }}</div><div class="value">{{ run.profit_loss_ratio ?? '—' }}</div></div></el-card></el-col>
    </el-row>

    <!-- P2-5：费用与摩擦面板（引擎侧已参数化：佣金/印花税卖出0.05%/过户费/滑点/涨跌停约束） -->
    <el-alert type="info" :closable="false" style="margin-bottom: 20px">
      {{ t('backtest.feePanel') }}
    </el-alert>

    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('backtest.symbolResults') }}</span>
          <el-button type="primary" @click="loadSummary" :loading="summaryLoading">{{ t('backtest.groupSummary') }}</el-button>
        </div>
      </template>
      <el-alert v-if="summary" type="info" :closable="false" style="margin-bottom: 12px">
        {{ t('backtest.groupAvg', { ret: summary.avg?.total_return_pct, wr: summary.avg?.win_rate, sh: summary.avg?.sharpe_ratio, n: summary.count }) }}
      </el-alert>
      <el-table :data="symbols" @row-click="goView">
        <el-table-column prop="symbol" :label="t('common.symbol')" show-overflow-tooltip />
        <el-table-column prop="status" :label="t('common.status')">
          <template #default="{ row }">
            <StatusTag :value="row.status" />
          </template>
        </el-table-column>
        <el-table-column :label="t('backtest.returnCol')" min-width="120">
          <template #default="{ row }">{{ row.result?.total_return_pct }}%</template>
        </el-table-column>
        <el-table-column :label="t('backtest.sharpe')" min-width="100">
          <template #default="{ row }">{{ row.result?.sharpe_ratio }}</template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="120">
          <template #default="{ row }">
            <el-button type="primary" @click.stop="goView(row)">{{ t('backtest.viewBtn') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </el-card>
</template>

<script setup>
import StatusTag from '../components/StatusTag.vue'
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getBacktestRun, verifyStrategy } from '../api'
import api from '../api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const run = ref({})
const symbols = ref([])
const summary = ref(null)
const summaryLoading = ref(false)

const goView = (row) => router.push(`/backtest/${route.params.id}/view/${row.symbol}`)
const loadSummary = async () => {
  summaryLoading.value = true
  try { summary.value = await api.get(`/backtest/${route.params.id}/summary`) } catch (e) { ElMessage.error(t('backtest.loadSummaryFailed')) }
  finally { summaryLoading.value = false }
}

const createLiveFromRun = () => {
  router.push({ path: '/live-task', query: { strategy: run.value.strategy_config_id } })
}
const markVerified = async () => {
  // wd-20 §1.2 验证门（修 19 号 P0 死按钮：原读不存在的 run.days→恒拦）：
  // span_days 后端单点派生；门槛常量 MIN_SPAN_DAYS=90 天 / MIN_TRADES=10 笔（05 §5.7 最低证据样本）
  const MIN_SPAN_DAYS = 90, MIN_TRADES = 10
  const days = run.value?.span_days ?? 0
  const trades = run.value?.total_trades ?? 0
  if (days < MIN_SPAN_DAYS || trades < MIN_TRADES) {
    ElMessage.warning(t('backtest.sampleThreshold', { d: days, s: trades }))
    return
  }
  try {
    await ElMessageBox.confirm(t('backtest.confirmVerify'), t('common.confirm'), { type: 'warning' })
    await verifyStrategy(run.value.strategy_config_id)
    ElMessage.success(t('backtest.markedVerified'))
  } catch (e) { ElMessage.error(t('backtest.markFailed')) }
}
onMounted(async () => {
  loading.value = true
  try {
    const data = await getBacktestRun(route.params.id)
    run.value = data
    symbols.value = data.symbols || []
  } catch (e) { ElMessage.error(t('backtest.loadDetailFailed')) }
  finally { loading.value = false }
})
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: var(--text-secondary); font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>
