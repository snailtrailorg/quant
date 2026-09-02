<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('dataSources.title') }}<el-button type="primary" @click="onAdd">{{ t('common.create') }}</el-button></div></template>
    <el-card v-if="usage.today && usage.today.length" shadow="never" style="margin-bottom: 12px">
      <div style="font-weight: bold; margin-bottom: 8px">{{ t('dataSources.usageTitle') }}</div>
      <el-table :data="usage.today">
        <el-table-column prop="provider" label="Provider" width="120" />
        <el-table-column prop="calls" :label="t('common.calls')" width="100" />
        <el-table-column prop="records" :label="t('common.records')" width="100" />
        <el-table-column :label="t('common.failures')" width="80">
          <template #default="{ row }"><el-tag :type="row.failures > 0 ? 'danger' : 'success'">{{ row.failures }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="avg_latency" :label="t('common.avgLatency')" width="110" />
      </el-table>
    </el-card>
    <el-table :data="sources">
      <el-table-column prop="provider" label="Provider" width="120" />
      <el-table-column prop="name" :label="t('common.name')" show-overflow-tooltip />
      <el-table-column :label="t('common.credential')" width="80">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="usage_limit" :label="t('common.dailyLimit')" width="80" />
      <el-table-column :label="t('common.enable')" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="260">
        <template #default="{ row }">
          <el-button type="primary" @click="onTest(row.id)" :loading="testing === row.id">{{ t('common.test') }}</el-button>
          <el-button type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="onDelete(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('dataSources.editTitle') : t('dataSources.addTitle')" width="560px">
      <el-form :model="form" label-width="120px">
      <el-form-item label="Provider"><el-input v-model="form.provider" :placeholder="t('dataSources.phProvider')" /></el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.credentialToken')"><el-input v-model="form.credentials" type="password" show-password :placeholder="t('common.phEditNoChange')" /></el-form-item>
      <el-form-item :label="t('common.dailyLimit')"><el-input-number v-model="form.usage_limit" :min="0" controls-position="right" /></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
      </template>
    </el-dialog>
    <template v-if="tushareRow">
      <el-divider />
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('dataSources.tierTitle') }}</h3>
      <div style="margin-bottom: 12px">
        <span style="margin-right: 8px">{{ t('dataSources.pointsTier') }}:</span>
        <el-radio-group :model-value="presets.current_tier" @change="onTierChange">
          <el-radio-button v-for="tr in presetTiers" :key="tr" :value="tr">{{ tr }} {{ t('dataSources.pointsUnit') }}</el-radio-button>
        </el-radio-group>
        <el-tag v-if="presets.current_tier == null" type="info" style="margin-left: 8px">{{ t('dataSources.noTier') }}</el-tag>
      </div>
      <el-collapse>
        <el-collapse-item :title="t('dataSources.rateTableTitle')" name="rates">
          <el-table :data="presets.apis" size="small" max-height="360">
            <el-table-column prop="api" label="API" min-width="110" />
            <el-table-column :label="t('dataSources.presetCol')" width="100">
              <template #default="{ row }">{{ fmtSec(row.preset ?? row.default) }}</template>
            </el-table-column>
            <el-table-column :label="t('dataSources.overrideCol')" width="210">
              <template #default="{ row }">
                <el-input-number v-model="row._edit" :min="0" :max="86400" :step="0.05" :precision="3" size="small" controls-position="right" style="width: 130px" />
                <el-button size="small" type="primary" style="margin-left: 6px" :disabled="!overrideDirty(row)" @click="saveOverride(row)">{{ t('common.save') }}</el-button>
              </template>
            </el-table-column>
            <el-table-column :label="t('dataSources.statusCol')" width="90">
              <template #default="{ row }">
                <el-tag :type="row.override != null ? 'warning' : 'info'" size="small">{{ row.override != null ? t('dataSources.tagOverride') : t('dataSources.tagDefault') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('dataSources.effectiveCol')" width="100">
              <template #default="{ row }">{{ fmtSec(row.effective) }}</template>
            </el-table-column>
            <el-table-column :label="t('common.action')" width="120">
              <template #default="{ row }">
                <el-button size="small" :disabled="row.override == null" @click="clearOverride(row)">{{ t('dataSources.resetPreset') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
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
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getDataSources, createDataSource, updateDataSource, deleteDataSource, testDataSource, getDataSourceUsage, getPointsPresets, setPointsTier, setRateLimitOverride } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const sources = ref([])
const usage = ref({ today: [], trend: [] })
const form = ref(emptyForm())
const saving = ref(false)
const dlg = ref(false)   // 编辑形态弹窗化（DESIGN 新立法）
const testing = ref(0)

// --- 积分档四层限流（tushare）：预设表 + 覆写 + 熔断参数 ---
const presets = ref({ current_tier: null, presets: {}, apis: [] })
const cb = ref({ fail_threshold: 5, reset_timeout: 60 })
const tushareRow = computed(() => sources.value.find(s => s.provider === 'tushare'))
const presetTiers = computed(() => Object.keys(presets.value.presets || {}).map(Number).sort((a, b) => a - b))

function emptyForm() {
  return { provider: 'tushare', name: '', credentials: '', usage_limit: null, enabled: true }
}

const load = async () => {
  try { sources.value = await getDataSources() } catch (e) { console.error(e) }
  loadPresets()   // 档位/覆写随数据源配置走，列表刷新后同步拉取
}
const loadUsage = async () => { try { usage.value = await getDataSourceUsage() } catch (e) { console.error(e) } }
onMounted(() => { load(); loadUsage() })

const onEdit = (row) => { form.value = { ...row, credentials: '' } ; dlg.value = true }
const resetForm = () => { form.value = emptyForm() }
const onAdd = () => { resetForm(); dlg.value = true }

const loadPresets = async () => {
  if (!tushareRow.value) return
  try {
    const p = await getPointsPresets('tushare')
    p.apis.forEach(a => { a._edit = a.override })   // 覆写编辑框初值=当前覆写（无则空）
    presets.value = p
    cb.value = { fail_threshold: p.circuit_breaker.fail_threshold, reset_timeout: p.circuit_breaker.reset_timeout }
  } catch (e) { console.debug('无积分档预设（provider 未注册或无配置）', e) }
}

const fmtSec = (v) => (v == null ? '-' : `${v}s`)

/** 客户端预览切档 diff（确认框展示"将变化项"；生效值后端 PUT 返回同款 diff） */
const previewDiff = (newTier) => {
  const p = presets.value
  const before = p.presets[String(p.current_tier)] || {}
  const after = p.presets[String(newTier)] || {}
  return p.apis
    .map(a => ({ api: a.api, b: a.override ?? before[a.api] ?? a.default, v: a.override ?? after[a.api] ?? a.default }))
    .filter(d => d.b !== d.v)
}

const onTierChange = async (newTier) => {
  const diff = previewDiff(newTier)
  const lines = diff.length
    ? diff.map(d => `${d.api}: ${fmtSec(d.b)} → ${fmtSec(d.v)}`).join('<br>')
    : t('dataSources.noChange')
  try {
    await ElMessageBox.confirm(lines, t('dataSources.tierConfirmTitle', { tier: newTier }),
      { dangerouslyUseHTMLString: true, type: 'warning' })
  } catch { return }   // 取消：model-value 仍挂旧档，无本地漂移
  try {
    await setPointsTier('tushare', newTier)
    ElMessage.success(t('dataSources.tierUpdated'))
    loadPresets()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

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

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateDataSource(form.value.id, form.value)
    else await createDataSource(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteDataSource(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testDataSource(id)
    if (r.ok) ElMessage.success(t('common.connectSuccess'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(t('common.testFailed')) }
  finally { testing.value = 0 }
}
</script>
