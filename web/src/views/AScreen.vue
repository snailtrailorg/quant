<template>
  <div class="screen-wrap">
    <ScreenPlanList class="left" :plans="presets" :loading="plansLoading" :active-id="activePresetId"
                    @create="openDlg(null)" @edit="openDlg" @apply="applyPreset" @changed="loadPresets" />
    <ContentCard :title="resultTitle" class="right">
      <template #actions>
        <ColumnSettings storage-key="cols.screener-astock" :columns="colDefs" v-model:visible="visible" />
        <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" class="pool">
          <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <IconBtn size="small" :icon="Plus"
                 :title="t('screener.addToPool') + (checked.size ? ' (' + checked.size + ')' : '')"
                 :disabled="!checked.size || !selectedPool" @click="addToPool" />
      </template>
      <TableShell :data="pagedRows" size="small" @selection-change="onSelChange" storage-key="screener-astock">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="ts_code" label="Code" min-width="110" />
        <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('close')" prop="close" :label="t('trading.price')" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('pe')" prop="pe" label="PE" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('pe_ttm')" prop="pe_ttm" :label="t('cols.peTtm')" min-width="114" class-name="num">
          <template #default="{ row }">{{ row.pe_ttm != null ? row.pe_ttm.toFixed(1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('pb')" prop="pb" label="PB" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('turnover')" prop="turnover" :label="t('cols.turnover')" min-width="101" class-name="num">
          <template #default="{ row }">{{ row.turnover != null ? row.turnover.toFixed(1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('market_cap')" prop="total_mv" :label="t('screener.marketCap')" min-width="110" class-name="num">
          <template #default="{ row }">{{ row.total_mv ? fmtCn(row.total_mv * 10000, 1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('circ_mv')" prop="circ_mv" :label="t('cols.circMv')" min-width="110" class-name="num">
          <template #default="{ row }">{{ row.circ_mv ? fmtCn(row.circ_mv * 10000, 1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('dv_ratio')" prop="dv_ratio" :label="t('cols.dvRatio')" min-width="90" class-name="num">
          <template #default="{ row }">{{ row.dv_ratio ?? '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('momentum')" prop="momentum_20" :label="t('cols.mom20')" min-width="90" class-name="num">
          <template #default="{ row }">{{ row.momentum_20 != null ? (row.momentum_20 * 100).toFixed(1) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('ma250_dev')" prop="price_vs_ma250" :label="t('cols.ma250Dev')" min-width="100" class-name="num">
          <template #default="{ row }">{{ row.price_vs_ma250 != null ? row.price_vs_ma250.toFixed(1) + '%' : '—' }}</template>
        </el-table-column>
      </TableShell>
      <el-pagination v-if="rows.length > pageSize" v-model:current-page="page" :page-size="pageSize" :total="rows.length"
        layout="prev, pager, next" class="pager" />
    </ContentCard>
    <ScreenPlanDialog :visible="dlgVisible" category="stock" :plan="editPlan" :fields="fields"
                      @close="dlgVisible = $event" @saved="loadPresets" @applydef="runDef" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
import { fmtCn } from '../utils/format'
import ContentCard from '../components/ContentCard.vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import ScreenPlanList from '../components/ScreenPlanList.vue'
import ScreenPlanDialog from '../components/ScreenPlanDialog.vue'
import { Plus } from '@element-plus/icons-vue'

const { t } = useI18n()
const rows = ref([])
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50
const presets = ref([])
const plansLoading = ref(false)
const fields = ref([])
const dlgVisible = ref(false)
const editPlan = ref(null)
const activePresetId = ref(null)
const activeName = ref('')

const resultTitle = computed(() => activeName.value
  ? t('screener.results') + ' · ' + activeName.value + ' (' + rows.value.length + ')'
  : t('screener.results') + ' (' + rows.value.length + ')')

const colDefs = computed(() => [
  { key: 'close', label: t('trading.price') },
  { key: 'pe', label: 'PE' },
  { key: 'pe_ttm', label: t('cols.peTtm') },
  { key: 'pb', label: 'PB' },
  { key: 'turnover', label: t('cols.turnover'), hidden: true },
  { key: 'market_cap', label: t('screener.marketCap') },
  { key: 'circ_mv', label: t('cols.circMv') },
  { key: 'dv_ratio', label: t('cols.dvRatio') },
  { key: 'momentum', label: t('cols.mom20') },
  { key: 'ma250_dev', label: t('cols.ma250Dev') },
])
const visible = ref([])
const colOn = k => visible.value.includes(k)
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }

const loadPresets = async () => {
  plansLoading.value = true
  try { presets.value = await api.get('/screen/presets?category=stock') || [] } catch { presets.value = [] }
  finally { plansLoading.value = false }
}
const loadFields = async () => { try { fields.value = (await api.get('/screen/fields?category=stock'))?.fields || [] } catch { fields.value = [] } }

const openDlg = (plan) => { editPlan.value = plan; dlgVisible.value = true }
const applyPreset = (p) => { activePresetId.value = p.id; activeName.value = p.name; runDef(p.definition) }
const runDef = async (def) => {
  try {
    const j = def.filter !== undefined
      ? await api.post('/screen/run-dsl', { category: 'stock', limit: 500, filter: def.filter || '', rank: def.rank || null })
      : await api.post('/screen/run', { category: 'stock', limit: 500, definition: def })
    rows.value = j.rows || []
    page.value = 1
    if (j.nulls_excluded) ElMessage.info(t('screener.nullsExcluded', { n: j.nulls_excluded }))
  } catch (e) { ElMessage.error(apiErrX(e)) }
}
const apiErrX = (e) => {
  const code = e?.response?.data?.code || e?.code
  return code ? t('err.' + code) : t('common.loadFailed')
}

const loadPools = async () => { try { pools.value = await api.get('/pool') || [] } catch {} }
const addToPool = async () => {
  if (!checked.value.size || !selectedPool.value) return
  const pool = pools.value.find(p => p.id === selectedPool.value)
  if (!pool) return
  const symbols = [...checked.value]
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

onMounted(() => { loadPresets(); loadFields(); loadPools() })
</script>

<style scoped>
.screen-wrap { display: flex; gap: var(--sp-3); align-items: flex-start; }
.left { width: 300px; flex-shrink: 0; }
.right { flex: 1; overflow: auto; }
.pool { width: 140px; }
.pager { margin-top: var(--sp-3); justify-content: flex-end; }
</style>
