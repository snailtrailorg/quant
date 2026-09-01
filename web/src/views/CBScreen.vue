<template>
  <!-- 05 §5.9 转债筛选:双低/溢价率/到期年限 条件+核心列+勾选入池 -->
  <div style="display: flex; gap: 16px">
    <el-card style="width: 260px; flex-shrink: 0">
      <template #header>{{ t('screener.cbFilters') }}</template>
      <el-form label-position="top" size="small">
        <el-form-item :label="t('screener.doubleLowMax')"><el-input-number v-model="f.double_low_max" :min="0" :step="5" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.premiumMax')"><el-input-number v-model="f.premium_max" :min="0" :step="5" :precision="0" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.remainingMin')"><el-input-number v-model="f.remaining_min" :min="0" :step="0.5" :precision="1" style="width:100%" /></el-form-item>
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
      <el-table :data="pagedRows" size="small" @selection-change="onSelChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="ts_code" label="Code" width="90" />
        <el-table-column prop="name" :label="t('screener.bondName')" min-width="80" show-overflow-tooltip />
        <el-table-column prop="stk_name" :label="t('screener.stkName')" min-width="80" show-overflow-tooltip />
        <el-table-column prop="bond_close" :label="t('trading.price')" width="65" class-name="num" />
        <el-table-column :label="t('screener.doubleLow')" width="70" class-name="num" sortable>
          <template #default="{ row }">{{ row.double_low?.toFixed(1) || '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('screener.premium')" width="70" class-name="num">
          <template #default="{ row }">
            <span v-if="row.premium_pct != null" :class="row.premium_pct >= 0 ? 'up' : 'down'">{{ row.premium_pct.toFixed(1) }}%</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="conv_price" :label="t('screener.convPrice')" width="65" class-name="num" />
        <el-table-column prop="maturity_date" :label="t('screener.maturity')" width="85">
          <template #default="{ row }">{{ (row.maturity_date || '').slice(0, 10) }}</template>
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

const { t } = useI18n()
const rows = ref([])
const loading = ref(false)
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50
const f = ref({ double_low_max: 0, premium_max: 0, remaining_min: 0 })

const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }

const load = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams(Object.entries(f.value).map(([k, v]) => [k, String(v)]).concat([['limit', '500']]))
    rows.value = await api.get(`/screen/cb?${params}`) || []
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
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || 'convertible',
                              symbolsStr: merged.join('\n'), description: pool.description || '',
                              minute_history_start: pool.minute_history_start || null })
    ElMessage.success(t('screener.added', { n: symbols.length }))
  } catch { ElMessage.error(t('common.failed')) }
}
onMounted(() => { load(); loadPools() })
</script>
