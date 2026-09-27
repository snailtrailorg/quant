<template>
  <!-- 批 62b（M7）：对账报表——shadow diff+白名单治理+SM 差集（/dataops 第四 tab） -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('quality.title') }}</span>
        <div style="display: flex; gap: 8px; align-items: center">
          <el-checkbox v-model="realOnly" @change="loadDiff">{{ t('quality.realOnly') }}</el-checkbox>
          <RefreshBtn :loading="loading" @refresh="loadAll" />
        </div>
      </div>
    </template>

    <el-alert type="info" :closable="false" style="margin-bottom: var(--sp-3)">
      {{ t('quality.selfCheckNote') }}
    </el-alert>

    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="6"><el-card shadow="never"><div style="color:var(--text-secondary)">{{ t('quality.diffRows') }}</div><div style="font-size: var(--fs-kpi)">{{ diffRows.length }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:var(--text-secondary)">{{ t('quality.realDiff') }}</div><div style="font-size: var(--fs-kpi); color: var(--critical)">{{ realCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:var(--text-secondary)">{{ t('quality.whitelisted') }}</div><div style="font-size: var(--fs-kpi); color: var(--warn-fill)">{{ diffRows.length - realCount }}</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never"><div style="color:var(--text-secondary)">{{ t('quality.lastRun') }}</div><div style="font-size: var(--fs-foot)">{{ lastRunAt }}</div></el-card></el-col>
    </el-row>

    <TableShell :data="diffRows" :loading="loading" style="width: 100%" fill :fill-reserve="60" storage-key="quality-diff">
      <el-table-column prop="symbol" :label="t('common.symbol')" min-width="110" />
      <el-table-column prop="trade_date" :label="t('quality.tradeDate')" min-width="100" />
      <el-table-column prop="field" :label="t('quality.field')" min-width="100">
        <template #default="{ row }">
          <el-tag v-if="row.field === '__missing__'" type="warning" size="small">{{ t('quality.missingRow') }}</el-tag>
          <el-tag v-else-if="row.field === '__extra__'" type="warning" size="small">{{ t('quality.extraRow') }}</el-tag>
          <span v-else>{{ row.field }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="main_val" :label="t('quality.mainVal')" min-width="100" />
      <el-table-column prop="backup_val" :label="t('quality.backupVal')" min-width="100" />
      <el-table-column prop="whitelist_hit" :label="t('quality.whitelistHit')" min-width="150">
        <template #default="{ row }">
          <span v-if="row.whitelist_hit" style="color: var(--warn-fill)">{{ row.whitelist_hit }}</span>
          <el-tag v-else type="danger" size="small">{{ t('quality.realDiffTag') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" :label="t('quality.checkedAt')" min-width="150" />
    </TableShell>

    <el-divider>{{ t('quality.whitelistSection') }}</el-divider>
    <TableShell :data="wlEntries" :loading="loading" style="width: 100%" storage-key="quality-wl">
      <el-table-column prop="id" label="ID" min-width="170" />
      <el-table-column prop="hits" :label="t('quality.hits')" min-width="80" />
      <el-table-column prop="hit_rate" :label="t('quality.hitRate')" min-width="100">
        <template #default="{ row }">
          <span v-if="row.hit_rate != null" :style="{ color: row.hit_rate >= 0.99 ? 'var(--critical)' : 'inherit' }">
            {{ (row.hit_rate * 100).toFixed(1) }}%
          </span>
          <span v-else style="color: var(--text-secondary)">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="last_verified" :label="t('quality.lastVerified')" min-width="170">
        <template #default="{ row }">
          <span v-if="row.last_verified">{{ String(row.last_verified).slice(0, 40) }}</span>
          <el-button v-else size="small" @click="verify(row.id)">{{ t('quality.verifyNow') }}</el-button>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" min-width="100">
        <template #default="{ row }">
          <el-button size="small" @click="verify(row.id)">{{ t('quality.verifyBtn') }}</el-button>
        </template>
      </el-table-column>
    </TableShell>

    <el-divider>{{ t('quality.smSection') }}</el-divider>
    <div style="font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-2)">{{ t('quality.smScopeNote') }}</div>
    <el-row :gutter="12">
      <el-col :span="12">
        <div style="font-weight: 600; margin-bottom: 4px">{{ t('quality.smOnly') }} ({{ (sm.sm_only || []).length }})</div>
        <div style="font-size: var(--fs-foot)">{{ (sm.sm_only || []).slice(0, 20).join(' ') || '—' }}</div>
      </el-col>
      <el-col :span="12">
        <div style="font-weight: 600; margin-bottom: 4px">{{ t('quality.staticOnly') }} ({{ (sm.static_only || []).length }})</div>
        <div style="font-size: var(--fs-foot)">{{ (sm.static_only || []).slice(0, 20).join(' ') || '—' }}</div>
      </el-col>
    </el-row>
  </el-card>
</template>

<script setup>
import TableShell from '../components/TableShell.vue'
import RefreshBtn from '../components/RefreshBtn.vue'
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getQualityDiff, getWhitelistStats, getSmReconcile, verifyWhitelist } from '../api'

const { t } = useI18n()
const loading = ref(false)
const diffRows = ref([])
const wlEntries = ref([])
const sm = ref({})
const realOnly = ref(false)

const realCount = computed(() => diffRows.value.filter(r => !r.whitelist_hit).length)
const lastRunAt = computed(() => diffRows.value[0]?.created_at || '—')

const loadDiff = async () => {
  try {
    const d = await getQualityDiff({ real_only: realOnly.value })
    diffRows.value = d.rows || []
  } catch {}
}
const loadAll = async () => {
  loading.value = true
  try {
    await Promise.all([loadDiff(),
      getWhitelistStats().then(d => { wlEntries.value = d.entries || [] }),
      getSmReconcile().then(d => { sm.value = d }).catch(() => {}),
    ])
  } finally { loading.value = false }
}
const verify = async (id) => {
  try {
    await verifyWhitelist(id, t('quality.verifyFromUi'))
    await getWhitelistStats().then(d => { wlEntries.value = d.entries || [] })
  } catch {}
}
onMounted(loadAll)
</script>
