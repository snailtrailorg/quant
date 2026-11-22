<template>
  <div>
    <el-card class="mb">
      <template #header>
        <div class="hd">
          <b>{{ t('sysmon.alertsActive') }}</b>
          <el-button size="small" @click="load">{{ t('common.refresh') }}</el-button>
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
          <div>
            <el-button size="small" @click="load">{{ t('common.refresh') }}</el-button>
            <el-button v-if="canClear" size="small" type="primary" plain @click="clear">{{ t('sysmon.clearHistory') }}</el-button>
          </div>
        </div>
      </template>
      <el-table :data="history" size="small" border>
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
import api, { apiErr, meOnce } from '../api'

const { t } = useI18n()
const active = ref([])
const history = ref([])
const perms = ref([])
const canClear = computed(() => perms.value.includes('system_config'))

const fmt = v => (v ? fmtTime.full(v) : '—')
const sev = s => (s === 'critical' ? t('sysmon.critical')
  : s === 'warning' ? t('sysmon.warning') : t('sysmon.normal'))
const sevType = s => (s === 'critical' ? 'danger' : s === 'warning' ? 'warning' : 'info')

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
