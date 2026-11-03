<template>
  <div class="screen-wrap">
    <ContentCard :title="t('screener.filters')" class="left">
      <ScreenFilterPanel category="listed_fund" @result="onResult" />
    </ContentCard>
    <ContentCard :title="t('screener.results') + ' (' + rows.length + ')'" class="right">
      <template #actions>
        <ColumnSettings storage-key="cols.screener-etf" :columns="colDefs" v-model:visible="visible" />
        <el-select v-model="selectedPool" size="small" :placeholder="t('screener.selectPool')" class="pool">
          <el-option v-for="p in pools" :key="p.id" :value="p.id" :label="p.name" />
        </el-select>
        <IconBtn size="small" :icon="Plus"
                 :title="t('screener.addToPool') + (checked.size ? ' (' + checked.size + ')' : '')"
                 :disabled="!checked.size || !selectedPool" @click="addToPool" />
      </template>
      <TableShell :data="pagedRows" size="small" @selection-change="onSelChange" storage-key="screener-etf">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="ts_code" label="Code" min-width="110" />
        <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('fund_type')" prop="fund_type" :label="t('screener.fundType')" min-width="90" />
        <el-table-column v-if="colOn('invest_type')" prop="invest_type" :label="t('cols.investType')" min-width="166" show-overflow-tooltip />
        <el-table-column v-if="colOn('management')" prop="management" :label="t('cols.manager')" min-width="140" show-overflow-tooltip />
        <el-table-column v-if="colOn('fund_scale')" prop="fund_scale" :label="t('screener.fundScale')" min-width="110" class-name="num">
          <template #default="{ row }">{{ row.fund_scale != null ? fmtCn(row.fund_scale, 1) : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('management_fee')" prop="management_fee" :label="t('screener.mgmtFee')" min-width="101" class-name="num">
          <template #default="{ row }">{{ row.management_fee != null ? row.management_fee.toFixed(2) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column v-if="colOn('tracking_error')" prop="tracking_error" :label="t('screener.trackingErr')" min-width="134" class-name="num">
          <template #default="{ row }">{{ row.tracking_error != null ? row.tracking_error.toFixed(2) : '—' }}</template>
        </el-table-column>
      </TableShell>
      <el-pagination v-if="rows.length > pageSize" v-model:current-page="page" :page-size="pageSize" :total="rows.length"
        layout="prev, pager, next" class="pager" />
    </ContentCard>
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
import ScreenFilterPanel from '../components/ScreenFilterPanel.vue'
import { Plus } from '@element-plus/icons-vue'

const { t } = useI18n()
const rows = ref([])
const checked = ref(new Set())
const pools = ref([])
const selectedPool = ref('')
const page = ref(1)
const pageSize = 50

const colDefs = computed(() => [
  { key: 'fund_type', label: t('screener.fundType') },
  { key: 'invest_type', label: t('cols.investType'), hidden: true },
  { key: 'management', label: t('cols.manager'), hidden: true },
  { key: 'fund_scale', label: t('screener.fundScale') },
  { key: 'management_fee', label: t('screener.mgmtFee') },
  { key: 'tracking_error', label: t('screener.trackingErr') },
])
const visible = ref([])
const colOn = k => visible.value.includes(k)

const pagedRows = computed(() => rows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const onSelChange = (sel) => { checked.value = new Set(sel.map(r => r.ts_code)) }
const onResult = (r) => { rows.value = r; page.value = 1 }

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
    await api.post('/pool', { id: pool.id, name: pool.name, category: pool.category || 'listed_fund',
                              symbolsStr: merged.join('\n'), description: pool.description || '' })
    ElMessage.success(t('screener.added', { n: symbols.length }))
  } catch { ElMessage.error(t('common.failed')) }
}

onMounted(loadPools)
</script>

<style scoped>
.screen-wrap { display: flex; gap: var(--sp-3); }
.left { width: 300px; flex-shrink: 0; }
.right { flex: 1; overflow: auto; }
.pool { width: 140px; }
.pager { margin-top: var(--sp-3); justify-content: flex-end; }
</style>
