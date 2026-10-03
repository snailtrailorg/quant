<template>
  <!-- 批 57 M2：数据路由页（29 号 §五"面"——weights 编辑+候选链试算器+审计）。
       批 88：**权重编辑卡下架**（安慰剂实证——排序键=熔断>position>score，而 quality 档案全仓恒空
       （仅 local_pg 常量有值）⇒ 外部候选 score≡0.5×Σw=常数，权重怎么改排序都不动；真优先级=position，
       编辑入口在集成中心·数据源 tab（set_positions）。后端 PATCH /routing/policies 与 _score 机制保留，
       M3+ quality 真实化时随该批恢复 UI。页面更名「路由诊断」，自 ops 组菜单降为数据管理页 tab。 -->
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <template #header><span style="font-weight: 600">{{ t('routing.rtDryTitle') }}</span></template>
      <el-alert type="info" :title="t('routing.rtDryTip')" :closable="false" style="margin-bottom: 12px" />
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px">
        <el-select v-model="dry.kind" size="small" style="width: 150px">
          <el-option v-for="k in dryKinds" :key="k" :label="kindLabel(k)" :value="k" />
        </el-select>
        <el-input v-model="dry.symbol" size="small" style="width: 200px" :placeholder="t('routing.rtSymbol')"
                  @keyup.enter="runDry" />
        <el-select v-model="dry.consumer" size="small" style="width: 130px">
          <el-option v-for="c in consumers" :key="c" :label="usageLabel(c)" :value="c" />
        </el-select>
        <el-button type="primary" size="small" :loading="dry.loading" @click="runDry">{{ t('routing.rtRun') }}</el-button>
      </div>
      <template v-if="dry.result">
        <el-alert v-if="!dry.result.chain.length" type="warning" :title="t('routing.rtChainEmpty')"
                  :closable="false" />
        <TableShell v-else :data="dry.result.chain" storage-key="routing-dryrun" size="small">
          <el-table-column prop="adapter" :label="t('routing.rtColAdapter')" min-width="120" />
          <el-table-column :label="t('routing.rtColLocal')" width="90">
            <template #default="{ row }">{{ row.is_local ? t('routing.rtYes') : t('routing.rtNo') }}</template>
          </el-table-column>
          <el-table-column prop="position" :label="t('routing.rtColPosition')" width="90" />
          <el-table-column prop="health" :label="t('routing.rtColHealth')" width="110"
                           :formatter="(r) => healthLabel(r.health)" />
          <el-table-column prop="score" :label="t('routing.rtColScore')" width="100"
                           :formatter="(r) => Number(r.score).toFixed(2)" />
        </TableShell>
      </template>
    </el-card>

    <el-card shadow="never">
      <template #header><span style="font-weight: 600">{{ t('routing.rtAuditTitle') }}</span></template>
      <TableShell :data="decisions" storage-key="routing-decisions" size="small">
        <el-table-column :label="t('routing.rtColTime')" min-width="150"
                         :formatter="(r) => fmtTime.full(r.ts)" />
        <el-table-column :label="t('routing.rtColCause')" width="120">
          <template #default="{ row }">{{ causeLabel(row.cause) }}</template>
        </el-table-column>
        <el-table-column :label="t('routing.rtColSummary')" min-width="220">
          <template #default="{ row }">
            <code style="font-size: var(--fs-foot)">{{ JSON.stringify(row.req_summary) }}</code>
          </template>
        </el-table-column>
        <el-table-column :label="t('routing.rtColChain')" min-width="160">
          <template #default="{ row }">
            <code style="font-size: var(--fs-foot)">{{ (row.chain || []).join(' → ') }}</code>
          </template>
        </el-table-column>
      </TableShell>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import api, { apiErr } from '../api'
import TableShell from '../components/TableShell.vue'
import { fmtTime } from '../utils/fmtTime'
import { ElMessage } from 'element-plus'

const { t, te } = useI18n()
const decisions = ref([])
const dryKinds = ['bar_daily', 'bar_minute']
const consumers = ['default', 'backtest', 'live', 'sync']
const dry = reactive({ kind: 'bar_daily', symbol: '', consumer: 'default', loading: false, result: null })

const load = async () => {
  try {
    const d = await api.get('/routing/decisions?limit=50')
    decisions.value = d.items || []
  } catch (e) { ElMessage.error(apiErr(e)) }
}

const runDry = async () => {
  if (!dry.symbol.trim()) return
  dry.loading = true
  try {
    dry.result = await api.get(`/routing/dry-run?kind=${dry.kind}&symbol=${encodeURIComponent(dry.symbol.trim())}&consumer=${dry.consumer}`)
    const ds = await api.get('/routing/decisions?limit=10')
    decisions.value = [{ ...ds.items?.[0], req_summary: safeParse(ds.items?.[0]?.req_summary) }].concat(decisions.value).slice(0, 50)
  } catch (e) { ElMessage.error(apiErr(e)) }
  finally { dry.loading = false }
}
const safeParse = (v) => (typeof v === 'string' ? JSON.parse(v) : v)

const usageLabel = (v) => {
  const key = `routing.rtUsage${v.charAt(0).toUpperCase()}${v.slice(1)}`
  return te(key) ? t(key) : v
}
const kindLabel = (v) => (v === 'bar_daily' ? t('routing.rtKindBarDaily') : v === 'bar_minute' ? t('routing.rtKindBarMinute') : v)
const healthLabel = (v) => {
  // 批 73：unknown=熔断状态读不出（缓存服务故障——fail-open 展示面，文案师词条）
  const map = { closed: 'Closed', open: 'Open', half_open: 'HalfOpen', unknown: 'Unknown' }
  const key = `routing.rtHealth${map[v] || 'Closed'}`
  return te(key) ? t(key) : v
}
const causeLabel = (c) => {
  // cause→词条键显式映射（snake_case→camelCase 拼接会得 'Skipbusy'≠'SkipBusy'——盲审自验修）
  const map = { resolve: 'Resolve', failover: 'Failover', skip_busy: 'SkipBusy', skip_unhealthy: 'SkipUnhealthy' }
  const key = `routing.rtCause${map[c] || ''}`
  return te(key) ? t(key) : c
}

onMounted(load)
</script>
