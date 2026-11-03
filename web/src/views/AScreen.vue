<template>
  <!-- 05 §5.9 A股筛选:条件区(左 240px 常驻)+结果表(勾选入池 symbolsStr)+方案保存 localStorage+市值亿 -->
  <div style="display: flex; gap: 16px">
    <el-card style="width: 260px; flex-shrink: 0">
      <template #header>{{ t('screener.filters') }}</template>
      <el-tabs model-value="basic" style="--el-tabs-header-height: 32px">
        <el-tab-pane :label="t('screener.tabBasic')" name="basic">
          <el-radio-group v-model="match" size="small" style="margin-bottom: 6px">
            <el-radio-button value="all">{{ t('screener.matchAll') }}</el-radio-button>
            <el-radio-button value="any">{{ t('screener.matchAny') }}</el-radio-button>
          </el-radio-group>
          <ScreenConditionRow v-for="(r, i) in rows" :key="i" :row="r" :fields="fields"
                              @upd="p => Object.assign(rows[i], p)" @del="rows.splice(i, 1)" />
          <el-button size="small" style="width: 100%; margin-bottom: 6px" @click="rows.push({ field: 'pe_ttm', op: '<', value: 30 })">+ {{ t('screener.addCond') }}</el-button>
          <el-button type="primary" style="width: 100%" @click="run" :loading="loading">{{ t('screener.run') }}</el-button>
        </el-tab-pane>
        <el-tab-pane :label="t('screener.tabAdvanced')" name="dsl">
          <div style="font-size: var(--fs-foot); color: var(--text-secondary); padding: 12px 0">{{ t('screener.dslComing') }}</div>
        </el-tab-pane>
      </el-tabs>
      <el-divider />
      <div style="font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-2)">{{ t('screener.savedPlans') }}</div>
      <div v-for="p in presets" :key="p.id" style="display: flex; gap: 4px; margin-bottom: 4px; align-items: center">
        <el-button size="small" text type="primary" style="flex: 1; justify-content: flex-start" @click="applyPreset(p)">{{ p.name }}</el-button>
        <el-tag v-if="p.source === 'builtin'" size="small" type="info">{{ t('screener.builtinTag') }}</el-tag>
        <el-button v-if="p.source !== 'builtin'" size="small" text type="danger" @click="delPreset(p)">×</el-button>
      </div>
      <el-button size="small" style="width: 100%" @click="savePreset">{{ t('screener.savePlan') }}</el-button>
    </el-card>

    <el-card style="flex: 1; overflow: auto">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>{{ t('screener.results') }} ({{ rows.length }})</span>
          <div>
            <!-- 批17 17B：列显示配置（代码/名称列恒显不进 defs） -->
            <span style="margin-right: var(--sp-2)"><ColumnSettings storage-key="cols.screener-astock" :columns="astockColDefs" v-model:visible="astockVisible" /></span>
            <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" style="width: 140px; margin-right: var(--sp-2)">
              <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
            </el-select>
            <IconBtn size="small" :icon="Plus" :title="t('screener.addToPool') + (checked.size ? ' (' + checked.size + ')' : '')" :disabled="!checked.size || !selectedPool" @click="addToPool" />
          </div>
        </div>
      </template>
      <!-- 批17 17A：TableShell 列宽拖拽+持久化 -->
      <TableShell :data="pagedRows" size="small" @selection-change="onSelChange" storage-key="screener-astock">
        <el-table-column type="selection" width="40" />
        <!-- 批16：列宽套档（min-width 权重，评估文档档位制）；+滚动市盈率（后端已返回） -->
        <el-table-column prop="ts_code" label="Code" min-width="110" />
        <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('close')" prop="close" :label="t('trading.price')" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('pe')" prop="pe" label="PE" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('pe_ttm')" prop="pe_ttm" :label="t('cols.peTtm')" min-width="114" class-name="num">
          <template #default="{ row }">{{ row.pe_ttm != null ? row.pe_ttm.toFixed(1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('pb')" prop="pb" label="PB" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('turnover')" prop="turnover" :label="t('screener.turnover')" min-width="101" class-name="num">
          <template #default="{ row }">{{ row.turnover?.toFixed(1) || '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('market_cap')" prop="total_mv" :label="t('screener.marketCap')" min-width="110" class-name="num">
          <template #default="{ row }">{{ fmtCn(row.total_mv * 10000, 1) }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('circ_mv')" prop="circ_mv" :label="t('screener.circMvMin').replace(/下限\(万元\)/, '')" min-width="110" class-name="num">
          <template #default="{ row }">{{ row.circ_mv ? fmtCn(row.circ_mv * 10000, 1) : '-' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('dv_ratio')" prop="dv_ratio" :label="t('screener.dvRatioMin').replace(/下限\(%\)/, '')" min-width="90" class-name="num">
          <template #default="{ row }">{{ row.dv_ratio ?? '-' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('momentum')" prop="momentum_20" :label="t('screener.mom20Min').replace(/下限.*/, '')" min-width="90" class-name="num">
          <template #default="{ row }">{{ row.momentum_20 != null ? (row.momentum_20 * 100).toFixed(1) + '%' : '-' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('ma250_dev')" prop="price_vs_ma250" :label="t('screener.ma250DevMin').replace(/下限\(%\)/, '')" min-width="100" class-name="num">
          <template #default="{ row }">{{ row.price_vs_ma250 != null ? row.price_vs_ma250.toFixed(1) + '%' : '-' }}</template>
        </el-table-column>
      </TableShell>
      <el-pagination v-if="rows.length > pageSize" v-model:current-page="page" :page-size="pageSize" :total="rows.length"
        layout="prev, pager, next" style="margin-top: var(--sp-3); justify-content: flex-end" />
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
import IconBtn from '../components/IconBtn.vue'
import { Plus } from '@element-plus/icons-vue'

const { t } = useI18n()
const rows = ref([])
const loading = ref(false)
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50
const f = ref({ pe_max: 0, pb_max: 0, mv_min: 0, turnover_min: 0,
  pe_ttm_max: 0, circ_mv_min: 0, dv_ratio_min: 0, ps_max: 0,
  exclude_st: false, list_days_min: 0, momentum_20_min: null, price_vs_ma250_min: null })
const plans = ref(JSON.parse(localStorage.getItem('screener_astock_plans') || '{}'))

// 批17 17B：列显示配置——代码/名称列恒显不进 defs；换手率=低频列默认隐
const astockColDefs = computed(() => [
  { key: 'close', label: t('trading.price') },
  { key: 'pe', label: 'PE' },
  { key: 'pe_ttm', label: t('cols.peTtm') },
  { key: 'pb', label: 'PB' },
  { key: 'turnover', label: t('screener.turnover'), hidden: true },
  { key: 'market_cap', label: t('screener.marketCap') },
  { key: 'circ_mv', label: t('screener.circMvMin').replace(/下限\(万元\)/, '') },   // 批164a：流通市值/股息/动量/年线乖离
  { key: 'dv_ratio', label: t('screener.dvRatioMin').replace(/下限\(%\)/, '') },
  { key: 'momentum', label: t('screener.mom20Min').replace(/下限.*/, '') },
  { key: 'ma250_dev', label: t('screener.ma250DevMin').replace(/下限\(%\)/, '') },
])
const astockVisible = ref([])
const colOn = k => astockVisible.value.includes(k)

const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }

const load = async () => {
  loading.value = true
  try {
    const params = new URLSearchParams(Object.entries(f.value)
      .filter(([, v]) => v !== null && v !== undefined && v !== false && v !== 0)   // 批164a：null/开关false/0 全=不过滤，不占 URL
      .map(([k, v]) => [k, String(v)]).concat([['limit', '500']]))
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
  // 批 131：去重键仍按裸码，但**下发必须带后缀**（existing 是 vt 形态、勾选行 ts_code 是 ts 形态，
  // 后端归一为 vt 入库）。原实现把 existing 去后缀后下发 ⇒ 池子一非空就发裸码 ⇒ 后端 SYMBOL_INVALID 400。
  const _key = (s) => (s || '').split('.')[0]
  const _existing = (pool.symbols || []).filter(Boolean)
  const _seen = new Set(_existing.map(_key))
  const merged = [..._existing, ...symbols.filter(s => !_seen.has(_key(s)))]
  try {
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || 'astock',
                              symbolsStr: merged.join('\n'), description: pool.description || '' })
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
