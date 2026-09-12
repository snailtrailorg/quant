<template>
  <!-- 05 §5.9 ETF 筛选:规模/费率/跟踪误差 条件+核心列+勾选入池 -->
  <div style="display: flex; gap: 16px">
    <el-card style="width: 260px; flex-shrink: 0">
      <template #header>{{ t('screener.etfFilters') }}</template>
      <el-form label-position="top" size="small">
        <el-form-item :label="t('screener.scaleMin')"><el-input-number v-model="f.scale_min" :min="0" :step="10" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.feeMax')"><el-input-number v-model="f.fee_max" :min="0" :step="0.1" :precision="2" style="width:100%" /></el-form-item>
        <el-button type="primary" style="width: 100%" @click="load" :loading="loading">{{ t('screener.run') }}</el-button>
      </el-form>
    </el-card>

    <el-card style="flex: 1; overflow: auto">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('screener.results') }} ({{ rows.length }})</span>
          <div>
            <!-- 批17 17B：列显示配置（代码/名称列恒显不进 defs） -->
            <span style="margin-right: var(--sp-2)"><ColumnSettings storage-key="cols.screener-etf" :columns="etfColDefs" v-model:visible="etfVisible" /></span>
            <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" style="width: 140px; margin-right: var(--sp-2)">
              <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
            <el-button type="primary" size="small" :disabled="!checked.size || !selectedPool" @click="addToPool">
              {{ t('screener.addToPool') }}{{ checked.size ? ` (${checked.size})` : '' }}
            </el-button>
          </div>
        </div>
      </template>
      <!-- 批17 17A：TableShell 列宽拖拽+持久化 -->
      <TableShell :data="pagedRows" size="small" @selection-change="onSelChange" storage-key="screener-etf">
        <el-table-column type="selection" width="40" />
        <!-- 批16：列宽套档；+管理人/投资类型（后端已返回未显示） -->
        <el-table-column prop="ts_code" label="Code" min-width="110" />
        <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('fund_type')" prop="fund_type" :label="t('screener.fundType')" min-width="90" />
        <el-table-column v-if="colOn('invest_type')" prop="invest_type" :label="t('cols.investType')" min-width="100" show-overflow-tooltip />
        <el-table-column v-if="colOn('management')" prop="management" :label="t('cols.manager')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('fund_scale')" :label="t('screener.fundScale')" min-width="110" class-name="num">
          <template #default="{ row }">{{ row.fund_scale != null ? fmtCn(row.fund_scale, 1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('management_fee')" :label="t('screener.mgmtFee')" min-width="80" class-name="num">
          <template #default="{ row }">{{ row.management_fee != null ? row.management_fee.toFixed(2) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('tracking_error')" :label="t('screener.trackingErr')" min-width="80" class-name="num">
          <template #default="{ row }">{{ row.tracking_error != null ? row.tracking_error.toFixed(2) : '—' }}</template>
        </el-table-column>
      </TableShell>
      <el-pagination v-if="rows.length > pageSize" v-model:current-page="page" :page-size="pageSize" :total="rows.length"
        layout="prev, pager, next" style="margin-top: 12px; justify-content: flex-end" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtCn } from '../utils/format'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'

const { t } = useI18n()
const rows = ref([])
const loading = ref(false)
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50
const f = ref({ scale_min: 0, fee_max: 0 })

// 批17 17B：列显示配置——代码/名称列恒显不进 defs；投资类型/管理人=低频属性列默认隐
const etfColDefs = computed(() => [
  { key: 'fund_type', label: t('screener.fundType') },
  { key: 'invest_type', label: t('cols.investType'), hidden: true },
  { key: 'management', label: t('cols.manager'), hidden: true },
  { key: 'fund_scale', label: t('screener.fundScale') },
  { key: 'management_fee', label: t('screener.mgmtFee') },
  { key: 'tracking_error', label: t('screener.trackingErr') },
])
const etfVisible = ref([])
const colOn = k => etfVisible.value.includes(k)

const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }

const load = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams(Object.entries(f.value).map(([k, v]) => [k, String(v)]).concat([['limit', '500']]))
    rows.value = await api.get(`/screen/etf?${params}`) || []
  } catch { rows.value = [] }
  finally { loading.value = false }
}
const loadPools = async () => { try { pools.value = await api.get('/pool') || [] } catch {} }
const addToPool = async () => {
  if (!checked.value.size || !selectedPool.value) return
  const pool = pools.value.find(p => p.id === selectedPool.value)
  if (!pool) return
  const symbols = [...checked.value]
  const existing = (pool.symbols || []).map(s => (s || '').split('.')[0])
  const merged = [...new Set([...existing, ...symbols])]
  try {
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || 'etf',
                              symbolsStr: merged.join('\n'), description: pool.description || '',
                              minute_history_start: pool.minute_history_start || null })
    ElMessage.success(t('screener.added', { n: symbols.length }))
  } catch { ElMessage.error(t('common.failed')) }
}
onMounted(() => { load(); loadPools() })
</script>
