<template>
  <div class="screen-wrap">
    <ScreenPlanList class="left" :plans="presets" :loading="plansLoading" :active-id="activePresetId"
                    @create="openDlg(null)" @edit="openDlg" @apply="applyPreset" @changed="loadPresets" />
    <ContentCard :title="resultTitle" class="right">
      <template #actions>
        <ColumnSettings storage-key="cols.screener-cb" :columns="colDefs" v-model:visible="visible" />
        <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" class="pool">
          <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <IconBtn size="small" :icon="Plus"
                 :title="t('screener.addToPool') + (checked.size ? ' (' + checked.size + ')' : '')"
                 :disabled="!checked.size || !selectedPool" @click="addToPool" />
      </template>
      <TableShell :data="pagedRows" @selection-change="onSelChange" storage-key="screener-cb">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="vt_symbol" label="Code" min-width="110" />
        <el-table-column prop="name" :label="t('screener.bondName')" min-width="120" show-overflow-tooltip />
        <el-table-column prop="stk_code" :label="t('cols.underlyingCode')" min-width="184" />
        <el-table-column v-if="colOn('stk_name')" prop="stk_name" :label="t('screener.stkName')" min-width="120" show-overflow-tooltip />
        <el-table-column v-if="colOn('bond_close')" prop="bond_close" :label="t('trading.price')" min-width="80" class-name="num" />
        <el-table-column v-if="colOn('stk_close')" prop="stk_close" :label="t('cols.underlyingPrice')" min-width="175" class-name="num">
          <template #default="{ row }">{{ row.stk_close != null ? row.stk_close.toFixed(2) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('double_low')" prop="double_low" :label="t('screener.doubleLow')" min-width="90" class-name="num" sortable>
          <template #default="{ row }">{{ row.double_low != null ? row.double_low.toFixed(1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('premium')" prop="premium_pct" :label="t('screener.premium')" min-width="92" class-name="num">
          <template #default="{ row }">
            <span v-if="row.premium_pct != null" :class="row.premium_pct >= 0 ? 'up' : 'down'">{{ row.premium_pct.toFixed(1) }}%</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column v-if="colOn('conv_price')" prop="conv_price" :label="t('screener.convPrice')" min-width="92" class-name="num" />
        <el-table-column v-if="colOn('maturity_date')" prop="maturity_date" :label="t('screener.maturity')" min-width="110">
          <template #default="{ row }">{{ (row.maturity_date || '').slice(0, 10) }}</template>
        </el-table-column>
      </TableShell>
      <el-pagination v-if="rows.length > pageSize" v-model:current-page="page" :page-size="pageSize" :total="rows.length"
        layout="prev, pager, next" class="pager" />
    </ContentCard>
    <ScreenPlanDialog :visible="dlgVisible" category="convertible" :plan="editPlan" :fields="fields"
                      @close="dlgVisible = $event" @saved="loadPresets" @applydef="runDef" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
import ContentCard from '../components/ContentCard.vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import ScreenPlanList from '../components/ScreenPlanList.vue'
import ScreenPlanDialog from '../components/ScreenPlanDialog.vue'
import { Plus } from '@element-plus/icons-vue'

const { t } = useI18n()
const CAT = 'convertible'
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
  { key: 'stk_name', label: t('screener.stkName') },
  { key: 'bond_close', label: t('trading.price') },
  { key: 'stk_close', label: t('cols.underlyingPrice') },
  { key: 'double_low', label: t('screener.doubleLow') },
  { key: 'premium', label: t('screener.premium') },
  { key: 'conv_price', label: t('screener.convPrice'), hidden: true },
  { key: 'maturity_date', label: t('screener.maturity') },
])
const visible = ref([])
const colOn = k => visible.value.includes(k)
const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.vt_symbol)) }

const loadPresets = async () => {
  plansLoading.value = true
  try { presets.value = await api.get('/screen/presets?category=' + CAT) || [] } catch { presets.value = [] }
  finally { plansLoading.value = false }
}
const loadFields = async () => { try { fields.value = (await api.get('/screen/fields?category=' + CAT))?.fields || [] } catch { fields.value = [] } }

const openDlg = (plan) => { editPlan.value = plan; dlgVisible.value = true }
const applyPreset = (p) => { activePresetId.value = p.id; activeName.value = p.name; runDef(p.definition) }
const runDef = async (def) => {
  try {
    const j = def.filter !== undefined
      ? await api.post('/screen/run-dsl', { category: CAT, limit: 500, filter: def.filter || '', rank: def.rank || null })
      : await api.post('/screen/run', { category: CAT, limit: 500, definition: def })
    rows.value = j.rows || []
    page.value = 1
    if (j.nulls_excluded) ElMessage.info(t('screener.nullsExcluded', { n: j.nulls_excluded }))
  } catch (e) {
    const code = e?.response?.data?.code || e?.code
    ElMessage.error(code ? t('err.' + code) : t('common.loadFailed'))
  }
}

const loadPools = async () => { try { pools.value = await api.get('/pool') || [] } catch {} }
const addToPool = async () => {
  if (!checked.value.size || !selectedPool.value) return
  const pool = pools.value.find(p => p.id === selectedPool.value)
  if (!pool) return
  const symbols = [...checked.value]
  const _existing = (pool.symbols || []).filter(Boolean)
  const _seen = new Set(_existing)          // 批175f：池与选股行同为 vt，直接精确去重（不再剥后缀）
  const merged = [..._existing, ...symbols.filter(s => !_seen.has(s))]
  try {
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || CAT,
                              symbolsStr: merged.join('\n'), description: pool.description || '' })
    ElMessage.success(t('screener.added', { n: symbols.length }))
  } catch { ElMessage.error(t('common.failed')) }
}

onMounted(() => { loadPresets(); loadFields(); loadPools() })
</script>

<style scoped>
.screen-wrap { display: flex; gap: var(--el-main-padding, 20px); align-items: flex-start; }
.left { width: 300px; flex-shrink: 0; }
.right { flex: 1; overflow: auto; }
.pool { width: 140px; }
.pager { margin-top: var(--sp-3); justify-content: flex-end; }
</style>
