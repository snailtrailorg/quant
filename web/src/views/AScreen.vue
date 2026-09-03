<template>
  <!-- 05 §5.9 A股筛选:条件区(左 240px 常驻)+结果表(勾选入池 symbolsStr)+方案保存 localStorage+市值亿 -->
  <div style="display: flex; gap: 16px">
    <el-card style="width: 260px; flex-shrink: 0">
      <template #header>{{ t('screener.filters') }}</template>
      <el-form label-position="top" size="small">
        <el-form-item :label="t('screener.peMax')"><el-input-number v-model="f.pe_max" :min="0" :step="5" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.pbMax')"><el-input-number v-model="f.pb_max" :min="0" :step="0.5" :precision="1" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.mvMin')"><el-input-number v-model="f.mv_min" :min="0" :step="50" style="width:100%" /></el-form-item>
        <el-form-item :label="t('screener.turnoverMin')"><el-input-number v-model="f.turnover_min" :min="0" :step="0.5" :precision="1" style="width:100%" /></el-form-item>
        <el-button type="primary" style="width: 100%" @click="load" :loading="loading">{{ t('screener.run') }}</el-button>
      </el-form>
      <el-divider />
      <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 6px">{{ t('screener.savedPlans') }}</div>
      <div v-for="(plan, name) in plans" :key="name" style="display: flex; gap: 4px; margin-bottom: 4px">
        <el-button size="small" text type="primary" @click="applyPlan(name)">{{ name }}</el-button>
        <el-button size="small" text type="danger" @click="delPlan(name)">×</el-button>
      </div>
      <el-button size="small" style="width: 100%" @click="savePlan">{{ t('screener.savePlan') }}</el-button>
    </el-card>

    <el-card style="flex: 1; overflow: auto">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('screener.results') }} ({{ rows.length }})</span>
          <div>
            <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" style="width: 140px; margin-right: var(--sp-2)">
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
        <el-table-column prop="ts_code" label="Code" width="100" />
        <el-table-column prop="name" :label="t('common.name')" min-width="90" show-overflow-tooltip />
        <el-table-column prop="close" :label="t('trading.price')" width="70" class-name="num" />
        <el-table-column prop="pe" label="PE" width="60" class-name="num" />
        <el-table-column prop="pb" label="PB" width="55" class-name="num" />
        <el-table-column :label="t('screener.turnover')" width="60" class-name="num">
          <template #default="{ row }">{{ row.turnover?.toFixed(1) || '—' }}</template>
        </el-table-column>
        <el-table-column :label="t('screener.marketCap')" width="90" class-name="num">
          <template #default="{ row }">{{ fmtCn(row.total_mv * 10000, 1) }}</template>
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
const f = ref({ pe_max: 0, pb_max: 0, mv_min: 0, turnover_min: 0 })
const plans = ref(JSON.parse(localStorage.getItem('screener_astock_plans') || '{}'))

const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }

const load = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams(Object.entries(f.value).map(([k, v]) => [k, String(v)]).concat([['limit', '500']]))
    rows.value = await api.get(`/screen/astock?${params}`) || []
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
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || 'astock',
                              symbolsStr: merged.join('\n'), description: pool.description || '',
                              minute_history_start: pool.minute_history_start || null })
    ElMessage.success(t('screener.added', { n: symbols.length }))
  } catch { ElMessage.error(t('common.failed')) }
}
const savePlan = () => {
  const name = prompt(t('screener.planName'))
  if (!name) return
  plans.value[name] = { ...f.value }
  localStorage.setItem('screener_astock_plans', JSON.stringify(plans.value))
  ElMessage.success(t('common.success'))
}
const applyPlan = (name) => { f.value = { ...plans.value[name] }; load() }
const delPlan = (name) => { delete plans.value[name]; localStorage.setItem('screener_astock_plans', JSON.stringify(plans.value)) }

onMounted(() => { load(); loadPools() })
</script>
