<template>
  <div>
    <el-card>
      <template #header>{{ t('risk.state') }}</template>
      <el-alert :title="state.halted ? t('risk.halted') : t('risk.normal')"
                :type="state.halted ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />
      <el-descriptions :column="1" border>
        <el-descriptions-item label="熔断原因">{{ state.reason || '—' }}</el-descriptions-item>
        <el-descriptions-item label="总回撤上限">{{ ((state.rules?.global?.max_drawdown || 0.15) * 100).toFixed(0) }}%</el-descriptions-item>
        <el-descriptions-item label="单日亏损上限">{{ ((state.rules?.global?.daily_loss_limit || 0.05) * 100).toFixed(0) }}%</el-descriptions-item>
        <el-descriptions-item label="加密杠杆上限">{{ state.rules?.crypto?.leverage_max || 5 }}x</el-descriptions-item>
      </el-descriptions>
      <div style="margin-top: 20px; display: flex; gap: 12px">
        <el-button type="danger" @click="onHalt" :disabled="state.halted">{{ t('risk.halt') }}</el-button>
        <el-button type="success" @click="onResume" :disabled="!state.halted">{{ t('risk.resume') }}</el-button>
      </div>
    </el-card>

    <!-- P2-1 实盘三级开关分项 -->
    <el-card style="margin-top: 20px">
      <template #header>实盘交易开关（三级第二级：Web 分项）</template>
      <el-table :data="liveTradingMarkets" stripe>
        <el-table-column prop="market" label="市场" width="150" />
        <el-table-column prop="label" label="说明" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => onToggleLive(row.market, v)" :loading="row.loading" />
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        三级开关（AND）：.env ENABLE_LIVE_TRADING 总闸 + 此处分项 + 策略 enabled+backtest_verified。任一关即拒单。
      </el-alert>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox, ElMessage } from 'element-plus'
import { getRiskState, riskHalt, riskResume, getLiveTrading, updateLiveTrading } from '../api'

const { t } = useI18n()
const state = ref({ halted: false, reason: '', rules: { global: { max_drawdown: 0.15, daily_loss_limit: 0.05 }, crypto: { leverage_max: 5 } } })
const liveTradingMarkets = ref([
  { market: 'convertible', label: '可转债', enabled: false, loading: false },
  { market: 'etf', label: 'ETF', enabled: false, loading: false },
  { market: 'astock', label: 'A股', enabled: false, loading: false },
  { market: 'binance_perp', label: '币安永续', enabled: false, loading: false },
  { market: 'okx_perp', label: 'OKX 永续', enabled: false, loading: false },
])

const load = async () => { state.value = await getRiskState() }
const loadLive = async () => {
  try {
    const data = await getLiveTrading()
    if (Array.isArray(data)) {
      liveTradingMarkets.value.forEach(m => {
        const found = data.find(d => d.market === m.market)
        if (found) m.enabled = found.enabled
      })
    } else if (data && typeof data === 'object') {
      liveTradingMarkets.value.forEach(m => { m.enabled = !!data[m.market] })
    }
  } catch (e) { /* 无配置时全 false */ }
}
const onToggleLive = async (market, enabled) => {
  const row = liveTradingMarkets.value.find(m => m.market === market)
  row.loading = true
  try {
    await updateLiveTrading(market, enabled)
    row.enabled = enabled
    ElMessage.success(`${market} ${enabled ? '已开启' : '已关闭'}`)
  } catch (e) {
    ElMessage.error('切换失败')
  } finally { row.loading = false }
}

const onHalt = async () => {
  await ElMessageBox.confirm(t('risk.confirmHalt'), { type: 'warning' })
  await riskHalt(); ElMessage.success('已熔断'); load()
}
const onResume = async () => {
  await ElMessageBox.confirm(t('risk.confirmResume'), { type: 'warning' })
  await riskResume(); ElMessage.success('已恢复'); load()
}
onMounted(async () => { await load(); await loadLive() })
</script>
