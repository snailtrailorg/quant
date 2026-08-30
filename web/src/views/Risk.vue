<template>
  <div>
    <SellGuardBanner v-if="state.halted" />

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

    <!-- P1-1 水位仪表（05 §5.3）：双进度条;75/90 变色纯 UI 档（引擎无预警语义） -->
    <el-card style="margin-top: 20px">
      <template #header>{{ t('risk.gaugeTitle') }}</template>
      <div v-for="g in gauges" :key="g.key" style="margin-bottom: 18px">
        <div style="display: flex; justify-content: space-between; margin-bottom: 4px">
          <span>{{ g.label }}</span>
          <span class="num" :style="{ color: g.color }">{{ (g.value * 100).toFixed(1) }}% / {{ (g.limit * 100).toFixed(0) }}%</span>
        </div>
        <el-progress :percentage="g.pct" :color="g.color" :stroke-width="14" :show-text="false" />
      </div>
      <!-- B#3 fail-closed 可见：快照年龄>300s 时明示拒 BUY -->
      <el-alert v-if="snapshotStale" type="error" :closable="false">
        {{ t('risk.snapshotStale', { s: Math.round(state.metrics?.snapshot_age_s ?? 0) }) }}
      </el-alert>
      <el-alert v-else-if="state.metrics && !state.metrics.available" type="error" :closable="false">{{ t('risk.snapshotMissing') }}</el-alert>
      <div v-else style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('risk.snapshotAge', { s: Math.round(state.metrics?.snapshot_age_s ?? 0) }) }}</div>
    </el-card>

    <!-- P2-1 实盘三级开关分项 -->
    <el-card style="margin-top: 20px">
      <template #header>{{ t('risk.liveSwitchTitle') }}</template>
      <!-- 链条打磨#21：.env 总闸状态（三级开关第一级——关则分项全无效） -->
      <el-alert v-if="masterEnabled === false" type="error" :closable="false" style="margin-bottom: 12px">
        {{ t('risk.masterOff') }}
      </el-alert>
      <el-table :data="liveTradingMarkets">
        <el-table-column prop="market" :label="t('risk.market')" width="150" />
        <el-table-column :label="t('risk.label')">
          <template #default="{ row }">{{ t(row.labelKey) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.status')" width="120">
          <template #default="{ row }">
            <!-- P1-1（05 §5.3 要点 5）：switch→按钮+确认弹窗（显示影响面） -->
            <el-button size="small" :type="row.enabled ? 'warning' : 'success'" :loading="row.loading"
                       @click="onToggleLive(row.market, !row.enabled)">
              {{ row.enabled ? t('risk.pauseBtn') : t('risk.enableBtn') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        {{ t('risk.switchHint') }}
      </el-alert>
    </el-card>

    <!-- P1-1 风控决策日志（05 §5.3 B#2）：拒单/覆写/放行三类可筛 -->
    <el-card style="margin-top: 20px">
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center">
          <span>{{ t('risk.logTitle') }}</span>
          <el-radio-group v-model="logFilter" size="small" @change="loadLog">
            <el-radio-button value="">{{ t('risk.logAll') }}</el-radio-button>
            <el-radio-button value="reject">{{ t('risk.logReject') }}</el-radio-button>
            <el-radio-button value="adjust">{{ t('risk.logAdjust') }}</el-radio-button>
            <el-radio-button value="approve">{{ t('risk.logApprove') }}</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <el-table :data="riskLogs" size="small" max-height="420">
        <el-table-column prop="ts" :label="t('common.time')" width="160" />
        <el-table-column :label="t('risk.logAction')" width="90">
          <template #default="{ row }">
            <el-tag :type="row.action === 'reject' ? 'danger' : row.action === 'adjust' ? 'warning' : 'success'" size="small">
              {{ { reject: t('risk.logReject'), adjust: t('risk.logAdjust'), approve: t('risk.logApprove') }[row.action] || row.action }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="symbol" label="Symbol" width="130" />
        <el-table-column prop="detail" :label="t('risk.logDetail')" show-overflow-tooltip />
        <el-table-column prop="severity" :label="t('risk.logSeverity')" width="90" />
      </el-table>
      <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: 8px">{{ t('risk.sellAlwaysNote') }}</div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox, ElMessage } from 'element-plus'
import { getRiskState, riskHalt, riskResume, getLiveTrading, updateLiveTrading } from '../api'
import SellGuardBanner from '../components/SellGuardBanner.vue'

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

// P1-1 水位仪表（05 §5.3：75/90 变色=纯 UI 档,不暗示引擎约束）
import { computed } from 'vue'
import api from '../api'
const riskLogs = ref([])
const logFilter = ref('')
const loadLog = async () => {
  try { riskLogs.value = (await api.get(`/risk/log${logFilter.value ? `?action=${logFilter.value}` : ''}`)).items || [] }
  catch { riskLogs.value = [] }
}
const _gaugeColor = pct => pct >= 90 ? 'var(--critical)' : pct >= 75 ? 'var(--warn-fill)' : 'var(--success)'
const gauges = computed(() => {
  const m = state.value.metrics || {}
  const rules = state.value.rules || {}
  const dd = m.total_drawdown ?? 0, dl = m.daily_loss ?? 0
  const ddl = rules.global?.max_drawdown ?? 0.15, dll = rules.global?.daily_loss_limit ?? 0.05
  return [
    { key: 'dd', label: t('risk.maxDrawdown'), value: dd, limit: ddl, pct: Math.min(dd / ddl * 100, 100), color: _gaugeColor(dd / ddl * 100) },
    { key: 'dl', label: t('risk.dailyLossLimit'), value: dl, limit: dll, pct: Math.min(dl / dll * 100, 100), color: _gaugeColor(dl / dll * 100) },
  ]
})
const snapshotStale = computed(() => (state.value.metrics?.snapshot_age_s ?? 0) > 300)
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
  // P1-1（05 §5.3 要点 5）：确认弹窗显示影响面
  try {
    await ElMessageBox.confirm(t(enabled ? 'risk.confirmEnable' : 'risk.confirmPause', { m: t(row.labelKey) }),
                               t('common.confirm'), { type: 'warning' })
  } catch { return }
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
onMounted(async () => { await load(); await loadLog(); await loadLive() })
</script>
