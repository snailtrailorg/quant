<template>
  <div>
    <el-card>
      <template #header>{{ t('risk.state') }}</template>
      <el-alert :title="state.halted ? t('risk.halted') : t('risk.normal')"
                :type="state.halted ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />
      <el-descriptions :column="1" border>
        <el-descriptions-item :label="t('risk.haltReason')">{{ state.reason || '—' }}</el-descriptions-item>
        <el-descriptions-item :label="t('risk.maxDrawdown')">{{ ((state.rules?.global?.max_drawdown || 0.15) * 100).toFixed(0) }}%</el-descriptions-item>
        <el-descriptions-item :label="t('risk.dailyLossLimit')">{{ ((state.rules?.global?.daily_loss_limit || 0.05) * 100).toFixed(0) }}%</el-descriptions-item>
        <el-descriptions-item :label="t('risk.leverageMax')">{{ state.rules?.crypto?.leverage_max || 5 }}x</el-descriptions-item>
      </el-descriptions>
      <div style="margin-top: 20px; display: flex; gap: 12px">
        <el-button type="danger" @click="onHalt" :disabled="state.halted">{{ t('risk.halt') }}</el-button>
        <el-button type="success" @click="onResume" :disabled="!state.halted">{{ t('risk.resume') }}</el-button>
      </div>
    </el-card>

    <!-- P2-1 实盘三级开关分项 -->
    <el-card style="margin-top: 20px">
      <template #header>{{ t('risk.liveSwitchTitle') }}</template>
      <!-- 链条打磨#21：.env 总闸状态（三级开关第一级——关则分项全无效） -->
      <el-alert v-if="masterEnabled === false" type="error" :closable="false" style="margin-bottom: 12px">
        {{ t('risk.masterOff') }}
      </el-alert>
      <el-table :data="liveTradingMarkets" stripe>
        <el-table-column prop="market" :label="t('risk.market')" width="150" />
        <el-table-column :label="t('risk.label')">
          <template #default="{ row }">{{ t(row.labelKey) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="120">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => onToggleLive(row.market, v)" :loading="row.loading" />
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        {{ t('risk.switchHint') }}
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
  { market: 'convertible', labelKey: 'risk.mConvertible', enabled: false, loading: false },
  { market: 'etf', labelKey: 'risk.mEtf', enabled: false, loading: false },
  { market: 'astock', labelKey: 'risk.mAstock', enabled: false, loading: false },
  { market: 'binance_perp', labelKey: 'risk.mBinancePerp', enabled: false, loading: false },
  { market: 'okx_perp', labelKey: 'risk.mOkxPerp', enabled: false, loading: false },
])

const load = async () => { state.value = await getRiskState() }
const masterEnabled = ref(null)
const loadLive = async () => {
  try {
    // 链条打磨#21：适配 {master_enabled, items} 形状（此前按数组/按 market 键对象解析→恒 false）
    const data = await getLiveTrading()
    masterEnabled.value = data?.master_enabled ?? null
    const items = Array.isArray(data?.items) ? data.items : []
    liveTradingMarkets.value = liveTradingMarkets.value.map(m => {
      const found = items.find(d => d.market === m.market)
      return { ...m, enabled: found ? !!found.enabled : false }
    })
  } catch (e) { /* 无配置时全 false */ }
}
const onToggleLive = async (market, enabled) => {
  const row = liveTradingMarkets.value.find(m => m.market === market)
  row.loading = true
  try {
    await updateLiveTrading(market, enabled)
    row.enabled = enabled
    ElMessage.success(`${t(row.labelKey)} ${enabled ? t('risk.switchedOn') : t('risk.switchedOff')}`)
  } catch (e) {
    ElMessage.error(t('risk.toggleFailed'))
  } finally { row.loading = false }
}

const onHalt = async () => {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt'), { type: 'warning' })
    await riskHalt(); ElMessage.success(t('risk.halted')); load()
  } catch (e) { console.error(e) }
}
const onResume = async () => {
  // H7（08 盲审#2）：恢复=输入确认(重新暴露风险才是强确认该在的地方);熔断保持轻确认
  try {
    const { value } = await ElMessageBox.prompt(
      t('risk.resumePromptTip'), t('risk.resumePromptTitle'),
      { type: 'warning', confirmButtonText: t('risk.resume') })
    if (value?.trim() !== 'RESUME') { ElMessage.warning(t('risk.resumeMismatch')); return }
    await riskResume(); ElMessage.success(t('risk.resumed')); load()
  } catch (e) { if (e?.response) console.error(e) }
}
onMounted(async () => { await load(); await loadLive() })
</script>
