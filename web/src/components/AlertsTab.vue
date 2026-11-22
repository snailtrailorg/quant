<template>
  <div>
    <el-card class="mb">
      <template #header>
        <div class="hd">
          <b>{{ t('sysmon.alertsActive') }}</b>
          <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
        </div>
      </template>
      <el-table :data="active" size="small" border>
        <el-table-column :label="t('sysmon.tsCol')" min-width="170">
          <template #default="{ row }">{{ fmt(row.ts) }}</template>
        </el-table-column>
        <el-table-column :label="t('sysmon.severityCol')" min-width="90">
          <template #default="{ row }"><el-tag :type="sevType(row.severity)" effect="dark" size="small">{{ sev(row.severity) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('sysmon.ruleCol')" prop="rule_id" min-width="150" />
        <el-table-column :label="t('sysmon.componentCol')" prop="component" min-width="160" />
        <el-table-column :label="t('sysmon.detailCol')" prop="detail" min-width="320" show-overflow-tooltip />
      </el-table>
    </el-card>

    <el-card>
      <template #header>
        <div class="hd">
          <b>{{ t('sysmon.alertsHistory') }}</b>
          <div style="display:flex;align-items:center;gap:var(--icon-gap)">
            <RowFilter v-model="histFilter" :groups="histFilterGroups" :title="t('common.filter')" />
            <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
            <IconBtn v-if="canClear" :icon="Delete" type="danger" :title="t('sysmon.clearHistory')" @click="clear" />
          </div>
        </div>
      </template>
      <el-table :data="histRows" size="small" border>
        <el-table-column :label="t('sysmon.tsCol')" min-width="170">
          <template #default="{ row }">{{ fmt(row.ts) }}</template>
        </el-table-column>
        <el-table-column :label="t('sysmon.resolvedAt')" min-width="170">
          <template #default="{ row }">{{ fmt(row.resolved_ts) }}</template>
        </el-table-column>
        <el-table-column :label="t('sysmon.severityCol')" min-width="90">
          <template #default="{ row }"><el-tag :type="sevType(row.severity)" effect="dark" size="small">{{ sev(row.severity) }}</el-tag></template>
        </el-table-column>
        <el-table-column :label="t('sysmon.ruleCol')" prop="rule_id" min-width="150" />
        <el-table-column :label="t('sysmon.componentCol')" prop="component" min-width="160" />
        <el-table-column :label="t('sysmon.detailCol')" prop="detail" min-width="320" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { fmtTime } from '../utils/fmtTime'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Refresh } from '@element-plus/icons-vue'
import api, { apiErr, meOnce } from '../api'
import IconBtn from './IconBtn.vue'
import RowFilter from './RowFilter.vue'

const { t } = useI18n()
const active = ref([])
const history = ref([])
const perms = ref([])
const canClear = computed(() => perms.value.includes('system_config'))

const fmt = v => (v ? fmtTime.full(v) : '—')
const sev = s => (s === 'critical' ? t('sysmon.critical')
  : s === 'warning' ? t('sysmon.warning') : t('sysmon.normal'))
const sevType = s => (s === 'critical' ? 'danger' : s === 'warning' ? 'warning' : 'info')

// 历史卡行筛选：级别 + 规则（选项从当前数据 distinct，空=全显）
const histFilter = ref({})
const histFilterGroups = computed(() => {
  const rules = [...new Set(history.value.map(r => r.rule_id).filter(Boolean))].sort()
  return [
    { key: 'sev', label: t('sysmon.severityCol'), options: [
      { value: 'critical', label: t('sysmon.critical') },
      { value: 'warning', label: t('sysmon.warning') }] },
    { key: 'rule', label: t('sysmon.ruleCol'), options: rules.map(x => ({ value: x, label: x })) },
  ]
})
const histRows = computed(() => {
  const f = histFilter.value
  const sev = f.sev ?? [], rule = f.rule ?? []
  return history.value.filter(r =>
    (!sev.length || sev.includes(r.severity)) && (!rule.length || rule.includes(r.rule_id)))
})

async function load() {
  try {
    const [a, h] = await Promise.all([api.get('/alerts/active'), api.get('/alerts/history')])
    active.value = a.items || []
    history.value = h.items || []
  } catch (e) { ElMessage.error(apiErr(e)) }
}
async function clear() {
  try {
    await ElMessageBox.confirm(t('sysmon.clearConfirm'), t('sysmon.clearHistory'), { type: 'warning' })
    const r = await api.delete('/alerts/history')
    ElMessage.success(t('sysmon.cleared', { n: r.deleted || 0 }))
    load()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(apiErr(e))
  }
}
onMounted(async () => {
  try { perms.value = (await meOnce())?.permissions || [] } catch { perms.value = [] }
  load()
})
</script>

<style scoped>
.mb { margin-bottom: var(--sp-3); }
.hd { display: flex; align-items: center; justify-content: space-between; }
</style>
