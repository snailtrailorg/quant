<template>
  <!-- 批55b:数据源页签=外部接口统一表·数据视图(27 号:页签=能力过滤视图)。
      表格+弹窗+拖拽归 InterfacesCard(两视图单源);本页保留数据页特有件:
      今日调用量卡片+Tushare 限速/熔断折叠面板(provider 键端点不变)。 -->
  <div>
    <el-card v-if="usage.today && usage.today.length" shadow="never" style="margin-bottom: 12px">
      <div style="font-weight: bold; margin-bottom: var(--sp-2)">{{ t('dataSources.usageTitle') }}</div>
      <TableShell :data="usage.today" storage-key="datasource-usage">
        <el-table-column prop="provider" label="Provider" min-width="120" />
        <el-table-column prop="calls" :label="t('common.calls')" min-width="100" />
        <el-table-column prop="records" :label="t('common.records')" min-width="100" />
        <el-table-column prop="failures" :label="t('common.failures')" min-width="101">
          <template #default="{ row }"><el-tag :type="row.failures > 0 ? 'danger' : 'success'">{{ row.failures }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="avg_latency" :label="t('common.avgLatency')" min-width="166" />
      </TableShell>
    </el-card>
    <InterfacesCard view="data" @loaded="onRowsChanged" />
    <template v-if="tushareExists">
      <el-divider />
      <el-collapse>
        <el-collapse-item :title="t('dataSources.rateTableTitle')" name="rates">
          <TableShell :data="presets.apis" size="small" max-height="360" storage-key="datasource-rates">
            <el-table-column prop="api" label="API" min-width="110" />
            <el-table-column prop="override" :label="t('dataSources.overrideCol')" width="210">
              <template #default="{ row }">
                <el-input-number v-model="row._edit" :min="0" :max="86400" :step="0.05" :precision="3" size="small" controls-position="right" style="width: 130px" />
                <el-button size="small" type="primary" style="margin-left: 6px" :disabled="!overrideDirty(row)" @click="saveOverride(row)">{{ t('common.save') }}</el-button>
              </template>
            </el-table-column>
            <el-table-column prop="status" :label="t('dataSources.statusCol')" width="90">
              <template #default="{ row }">
                <el-tag :type="row.override != null ? 'warning' : 'info'" size="small">{{ row.override != null ? t('dataSources.tagOverride') : t('dataSources.tagDefault') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="effective" :label="t('dataSources.effectiveCol')" width="110">
              <template #default="{ row }">{{ fmtSec(row.effective) }}</template>
            </el-table-column>
            <el-table-column prop="actions" :label="t('common.action')" width="120">
              <template #default="{ row }">
                <el-button size="small" :disabled="row.override == null" @click="clearOverride(row)">{{ t('dataSources.resetPreset') }}</el-button>
              </template>
            </el-table-column>
          </TableShell>
        </el-collapse-item>
        <el-collapse-item :title="t('dataSources.cbTitle')" name="cb">
          <el-form inline label-width="160px">
            <el-form-item :label="t('dataSources.cbFailThreshold')">
              <el-input-number v-model="cb.fail_threshold" :min="1" :max="1000" controls-position="right" />
            </el-form-item>
            <el-form-item :label="t('dataSources.cbResetTimeout')">
              <el-input-number v-model="cb.reset_timeout" :min="1" :max="86400" controls-position="right" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveCb">{{ t('common.save') }}</el-button>
            </el-form-item>
          </el-form>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import TableShell from '../components/TableShell.vue'
import InterfacesCard from '../components/InterfacesCard.vue'
import { apiErr, getDataSourceUsage, getRateLimits, setRateLimitOverride } from '../api'
import { ElMessage } from 'element-plus'

const { t } = useI18n()
const usage = ref({ today: [], trend: [] })

// 限速/熔断（provider 键端点不变）：数据域存在 tushare 行即显示——随 CRUD 联动（盲审 A-P1-2：旧快照式滞留）
const tushareExists = ref(false)
const presets = ref({ apis: [] })
const cb = ref({ fail_threshold: 5, reset_timeout: 60 })

const onRowsChanged = (allRows) => {
  tushareExists.value = (allRows || []).some(r => r.provider === 'tushare' && !(r.capabilities || []).includes('trading'))
  loadPresets()
}

onMounted(async () => {
  try { usage.value = await getDataSourceUsage() } catch (e) { console.error(e) }
})

const loadPresets = async () => {
  if (!tushareExists.value) return
  try {
    const p = await getRateLimits('tushare')
    p.apis.forEach(a => { a._edit = a.override })   // 覆写编辑框初值=当前覆写（无则空）
    presets.value = p
    cb.value = { fail_threshold: p.circuit_breaker.fail_threshold, reset_timeout: p.circuit_breaker.reset_timeout }
  } catch (e) { console.debug('无限速配置（provider 未注册或无配置）', e) }
}

const fmtSec = (v) => (v == null ? '-' : `${v}s`)
const overrideDirty = (row) => row._edit != null && row._edit !== row.override

const saveOverride = async (row) => {
  try {
    await setRateLimitOverride('tushare', { api_name: row.api, value: row._edit })
    ElMessage.success(t('dataSources.overrideSaved'))
    loadPresets()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

const clearOverride = async (row) => {
  try {
    await setRateLimitOverride('tushare', { api_name: row.api, value: null })
    ElMessage.success(t('dataSources.overrideCleared'))
    loadPresets()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

const saveCb = async () => {
  try {
    await setRateLimitOverride('tushare', { circuit_breaker: { fail_threshold: cb.value.fail_threshold, reset_timeout: cb.value.reset_timeout } })
    ElMessage.success(t('dataSources.cbSaved'))
    loadPresets()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}
</script>
