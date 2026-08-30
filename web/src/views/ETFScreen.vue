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
            <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" style="width: 140px; margin-right: 8px">
              <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
            <el-button type="primary" size="small" :disabled="!checked.size || !selectedPool" @click="addToPool">
              {{ t('screener.addToPool') }}{{ checked.size ? ` (${checked.size})` : '' }}
            </el-button>
          </div>
        </div>
      </template>
      <el-table :data="pagedRows" stripe size="small" @selection-change="onSelChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="ts_code" label="Code" width="100" />
        <el-table-column prop="name" :label="t('common.name')" width="120" show-overflow-tooltip />
        <el-table-column prop="fund_type" :label="t('screener.fundType')" width="80" />
        <el-table-column :label="t('screener.fundScale')" width="90" class-name="num">
          <template #default="{ row }">{{ row.fund_scale != null ? fmtCn(row.fund_scale, 1) : '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('screener.mgmtFee')" width="70" class-name="num">
          <template #default="{ row }">{{ row.management_fee != null ? row.management_fee.toFixed(2) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('screener.trackingErr')" width="70" class-name="num">
          <template #default="{ row }">{{ row.tracking_error != null ? row.tracking_error.toFixed(2) : '—' }}</template>
        </el-table-column>
      </el-table>
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

const { t } = useI18n()
const rows = ref([])
const loading = ref(false)
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50
const f = ref({ scale_min: 0, fee_max: 0 })

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
  const symbols = [...checked.value].map(c => c.replace(/\.\w+$/, ''))
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
