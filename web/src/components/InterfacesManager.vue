<template>
  <el-card>
    <template #header>
      <div class="hd">
        <span class="hd-title">{{ title }}</span>
        <div class="hd-acts">
          <RowFilter v-model="filter" :groups="filterGroups" :title="t('common.filter')" />
          <ColumnSettings :storage-key="'cols.iface.' + kind" :columns="colDefs" v-model:visible="visible" />
          <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
          <IconBtn v-if="canWrite" :icon="Plus" :title="t('common.create')" @click="openCreate" />
        </div>
      </div>
    </template>
    <TableShell :data="shown" :storage-key="'iface.' + kind">
      <el-table-column prop="code" :label="t('interfaces.colCode')" min-width="120">
        <template #default="{ row }"><code class="code">{{ row.code }}</code></template>
      </el-table-column>
      <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
      <el-table-column prop="provider" label="Provider" min-width="110" />
      <el-table-column prop="market" :label="t('interfaces.colMarket')" min-width="80">
        <template #default="{ row }">{{ marketName(row.market) }}</template>
      </el-table-column>
      <el-table-column v-if="kind === 'ds'" :label="t('interfaces.colCapabilities')" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="c in row.capabilities || []" :key="c" size="small" class="tag">{{ t('interfaces.cap_' + c) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-else :label="t('interfaces.colCarrier')" min-width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="carrierType(row.provider)" effect="plain">
            {{ carrier(row.provider) ? t('interfaces.carrierReady') : t('interfaces.carrierPending') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="kind !== 'ds'" :label="t('interfaces.colRuntime')" min-width="190">
        <template #default="{ row }">
          <span class="dot" :class="runClass(row.code)" />
          <span>{{ runText(row.code) }}</span>
          <span v-if="ageText(row.code)" class="muted"> · {{ ageText(row.code) }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="kind !== 'ds'" :label="t('interfaces.colConfig')" min-width="130">
        <template #default="{ row }">
          <span v-if="row.is_virtual" class="muted">—</span>
          <el-tag v-else-if="st(row.code)?.config_ok" size="small" type="success" effect="plain">{{ t('interfaces.configOk') }}</el-tag>
          <span v-else class="missing">{{ (st(row.code)?.config_missing || []).map(missingText).join(' / ') || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="kind === 'td'" :label="t('interfaces.colAccountKey')" min-width="120">
        <template #default="{ row }">
          <span v-if="row.is_virtual" class="tag-v">{{ t('interfaces.virtual') }}</span>
          <code v-else class="code">{{ maskAcct(row.account_key) }}</code>
        </template>
      </el-table-column>
      <el-table-column v-if="kind === 'td'" :label="t('interfaces.colMapping')" min-width="220">
        <template #default="{ row }">
          <template v-if="mapsOk[row.code]">
            <el-tag v-for="m in maps[row.code] || []" :key="m.md_code" size="small" class="tag">{{ m.md_code }} · p{{ m.priority }}</el-tag>
            <span v-if="!(maps[row.code] || []).length" class="missing">{{ t('interfaces.mappingNone') }}</span>
          </template>
          <span v-else class="muted">{{ t('interfaces.mappingLoading') }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="kind === 'md'" :label="t('interfaces.colRefs')" min-width="150">
        <template #default="{ row }">
          <el-tag v-for="r in refs[row.code] || []" :key="r.td_code" size="small" class="tag">{{ r.td_code }}</el-tag>
          <span v-if="!(refs[row.code] || []).length" class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.enable')" min-width="80">
        <template #default="{ row }">
          <el-switch :model-value="!!row.enabled" :disabled="!canWrite" @change="v => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column :label="t('common.credential')" min-width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="credType(row.has_credentials)" effect="plain">
            {{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('updated_at')" prop="updated_at" :label="t('common.updatedAt')" min-width="160">
        <template #default="{ row }">{{ row.updated_at ? fmtTime.m(row.updated_at) : '—' }}</template>
      </el-table-column>
      <el-table-column v-if="canWrite" :label="t('common.action')" min-width="130">
        <template #default="{ row }">
          <IconBtn v-if="kind === 'td'" size="small" :icon="SetUp" :title="t('interfaces.mappingEdit')" :disabled="mapsOk[row.code] !== true" @click="openMaps(row)" />
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')" @click="del(row)" />
        </template>
      </el-table-column>
    </TableShell>
  </el-card>

  <el-dialog v-model="dlg" :title="(form._create ? t('common.create') : t('common.edit')) + ' · ' + title" width="600" :close-on-click-modal="false">
    <el-form label-width="130">
      <el-form-item label="code">
        <el-input v-if="form._create" v-model="form.code" :placeholder="prefix + 'xxx'" />
        <el-input v-else :model-value="form.code" disabled />
      </el-form-item>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="Provider">
        <el-select v-if="form._create" v-model="form.provider" filterable @change="onProvider">
          <el-option v-for="p in providers" :key="p.provider" :value="p.provider" :label="p.provider">
            <span>{{ p.provider }}</span>
            <span class="opt-tag">{{ p.ready ? t('interfaces.carrierReady') : t('interfaces.carrierPending') }}</span>
          </el-option>
        </el-select>
        <el-input v-else :model-value="form.provider" disabled />
      </el-form-item>
      <el-form-item :label="t('interfaces.colMarket')">
        <el-input :model-value="marketName(form.market)" disabled />
      </el-form-item>
      <el-form-item v-if="kind === 'td'" :label="t('interfaces.colAccountKey')">
        <el-input v-model="form.account_key" :placeholder="t('interfaces.accountKeyPh')" />
      </el-form-item>
      <el-form-item v-if="kind === 'td'" :label="t('interfaces.virtual')">
        <el-switch v-model="form.is_virtual" :disabled="!form._create" />
      </el-form-item>
      <template v-for="f in curFields" :key="'c' + f.key">
        <el-form-item :label="labelOf(f)">
          <el-select v-if="f.type === 'select'" v-model="form.credentials[f.key]" style="width:100%">
            <el-option v-for="o in f.options || []" :key="o" :value="o" :label="o" />
          </el-select>
          <el-switch v-else-if="f.type === 'boolean'" v-model="form.credentials[f.key]" />
          <el-input v-else v-model="form.credentials[f.key]" :type="f.secret ? 'password' : 'text'" :show-password="!!f.secret" autocomplete="new-password" />
        </el-form-item>
      </template>
      <template v-for="f in curParams" :key="'p' + f.key">
        <el-form-item :label="labelOf(f)">
          <el-input v-if="f.type === 'number'" v-model.number="form.params[f.key]" />
          <el-switch v-else-if="f.type === 'boolean'" v-model="form.params[f.key]" />
          <el-input v-else v-model="form.params[f.key]" />
        </el-form-item>
      </template>
      <div v-if="!curFields.length && !curParams.length" class="muted note">{{ t('interfaces.noSchema') }}</div>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="mapDlg" :title="t('interfaces.mappingEdit') + ' · ' + mapCode" width="520">
    <el-table :data="mapForm" size="small" border>
      <el-table-column :label="t('interfaces.colMd')" min-width="200">
        <template #default="{ row }">
          <el-select v-model="row.md_code" filterable>
            <el-option v-for="m in mdOf" :key="m.code" :value="m.code" :label="m.code" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="priority" min-width="110">
        <template #default="{ row }"><el-input-number v-model="row.priority" :min="1" :max="99" size="small" /></template>
      </el-table-column>
      <el-table-column min-width="60">
        <template #default="{ $index }"><IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')" @click="mapForm.splice($index, 1)" /></template>
      </el-table-column>
    </el-table>
    <div class="map-foot"><IconBtn size="small" :icon="Plus" :title="t('common.create')" @click="mapForm.push({ md_code: '', priority: mapForm.length + 1 })" /></div>
    <template #footer>
      <el-button @click="mapDlg = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="saveMaps">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Edit, Delete, SetUp } from '@element-plus/icons-vue'
import api, { apiErr, meOnce } from '../api'
import { fmtTime } from '../utils/fmtTime'
import IconBtn from './IconBtn.vue'
import RowFilter from './RowFilter.vue'
import ColumnSettings from './ColumnSettings.vue'
import TableShell from './TableShell.vue'

const props = defineProps({ kind: { type: String, required: true } })
const emit = defineEmits(['loaded'])
const { t, te } = useI18n()

const rows = ref([]), providers = ref([]), perms = ref([])
const status = ref({}), maps = ref({}), mapsOk = ref({}), refs = ref({})
const filter = ref({}), visible = ref([])
const dlg = ref(false), saving = ref(false)
const form = ref(empty())
const mapDlg = ref(false), mapCode = ref(''), mapForm = ref([]), mapMarket = ref(''), mdRows = ref([])

const base = () => ({ ds: '/data-sources', md: '/md-interfaces', td: '/td-interfaces' }[props.kind])
const prefix = computed(() => ({ ds: 'ds-', md: 'md-', td: 'td-' }[props.kind]))
const title = computed(() => ({ ds: t('interfaces.cardTitleData'), md: t('interfaces.cardTitleMarket'), td: t('interfaces.cardTitleTrading') }[props.kind]))
const canWrite = computed(() => perms.value.includes('system_config'))
const st = code => status.value[code] || {}
const carrier = p => !!providers.value.find(v => v.provider === p)?.ready
const carrierType = p => (carrier(p) ? 'success' : 'warning')
const credType = ok => (ok ? 'success' : 'info')
const curProv = computed(() => providers.value.find(p => p.provider === form.value.provider) || {})
const curFields = computed(() => curProv.value.field_schema || [])
const curParams = computed(() => curProv.value.params_schema || [])
const mdOf = computed(() => mdRows.value.filter(m => !mapMarket.value || m.market === mapMarket.value))
const colDefs = computed(() => [{ key: 'updated_at', label: t('common.updatedAt'), hidden: true }])
const colOn = k => visible.value.includes(k)

function empty() { return { _create: true, code: '', name: '', provider: '', market: 'astock', credentials: {}, params: {}, account_key: '', is_virtual: false, enabled: false } }
function marketName(m) { return m === 'astock' ? t('interfaces.marketAstock') : m === 'crypto' ? t('interfaces.marketCrypto') : (m || '—') }
function labelOf(f) { return f.label_key && te(f.label_key) ? t(f.label_key) : (f.label || f.key) }
function maskAcct(v) { const s = String(v || ''); return s.length > 4 ? s.slice(0, 2) + '****' + s.slice(-2) : (s || '—') }
function missingText(m) { return ({ carrier: t('interfaces.missingCarrier'), credentials: t('interfaces.missingCredentials'), mapping: t('interfaces.missingMapping') }[m] || m) }
function runClass(code) { const s = st(code); return s.running ? (s.connected ? 'ok' : 'warn') : 'off' }
function runText(code) {
  const s = st(code)
  if (!s.running) return t('interfaces.runStopped')
  return [t('interfaces.runRunning'), s.working ? t('interfaces.runWorking') : t('interfaces.runIdle'), s.connected ? t('interfaces.runConnected') : t('interfaces.runDisconnected')].join(' · ')
}
function ageText(code) {
  const ts = Number(st(code).ts || 0)
  if (!ts) return ''
  const d = Math.max(0, Math.round(Date.now() / 1000 - ts))
  return d < 120 ? t('interfaces.ageSec', { n: d }) : t('interfaces.ageMin', { n: Math.round(d / 60) })
}
const filterGroups = computed(() => {
  const groups = [{ key: 'market', label: t('interfaces.colMarket'), options: [...new Set(rows.value.map(r => r.market).filter(Boolean))].map(m => ({ value: m, label: marketName(m) })) }]
  if (props.kind !== 'ds') {
    groups.push({ key: 'run', label: t('interfaces.colRuntime'), options: [
      { value: 'running', label: t('interfaces.runRunning') }, { value: 'stopped', label: t('interfaces.runStopped') }] })
    groups.push({ key: 'config', label: t('interfaces.colConfig'), options: [
      { value: 'ok', label: t('interfaces.configOk') }, { value: 'bad', label: t('interfaces.configBad') }] })
  } else {
    const caps = [...new Set(rows.value.flatMap(r => r.capabilities || []))]
    if (caps.length) groups.push({ key: 'cap', label: t('interfaces.colCapabilities'), options: caps.map(c => ({ value: c, label: t('interfaces.cap_' + c) })) })
  }
  return groups
})
const shown = computed(() => {
  const f = filter.value
  return rows.value.filter(r => {
    if (f.market?.length && !f.market.includes(r.market)) return false
    if (f.cap?.length && !(r.capabilities || []).some(c => f.cap.includes(c))) return false
    const s = st(r.code)
    if (f.run?.length && !f.run.includes(s.running ? 'running' : 'stopped')) return false
    if (f.config?.length && !f.config.includes(s.config_ok ? 'ok' : 'bad')) return false
    return true
  })
})
function onProvider(p) {
  const x = providers.value.find(v => v.provider === p)
  form.value.market = x?.market || 'astock'
  form.value.credentials = {}
  form.value.params = {}
}
function openCreate() { form.value = empty(); dlg.value = true }
function openEdit(r) {
  form.value = { ...empty(), _create: false, code: r.code, name: r.name, provider: r.provider, market: r.market,
    credentials: {}, params: { ...(r.params || {}) }, account_key: r.account_key || '', is_virtual: !!r.is_virtual, enabled: !!r.enabled }
  dlg.value = true
}
async function save() {
  const f = form.value
  const payload = { name: f.name, provider: f.provider, market: f.market, enabled: f.enabled,
    credentials: Object.fromEntries(Object.entries(f.credentials).filter(([, v]) => v !== '' && v != null)),
    params: f.params }
  if (props.kind === 'td') { payload.account_key = f.account_key || null; payload.is_virtual = f.is_virtual }
  if (f._create) payload.code = f.code
  saving.value = true
  try {
    if (f._create) await api.post(base(), payload)
    else await api.post(base() + '/' + f.code, payload)
    dlg.value = false; ElMessage.success(t('interfaces.saved')); load()
  } catch (e) { ElMessage.error(apiErr(e)) } finally { saving.value = false }
}
async function toggle(r, v) {
  try { await api.post(base() + '/' + r.code + '/enable', { enabled: v }); load() }
  catch (e) { ElMessage.error(apiErr(e)) }
}
async function del(r) {
  try {
    await ElMessageBox.confirm(t('interfaces.deleteConfirm', { code: r.code }), t('common.delete'), { type: 'warning' })
    await api.delete(base() + '/' + r.code); ElMessage.success(t('interfaces.deleted')); load()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(apiErr(e)) }
}
function openMaps(r) {
  mapCode.value = r.code; mapMarket.value = r.market
  mapForm.value = (maps.value[r.code] || []).map(m => ({ md_code: m.md_code, priority: m.priority }))
  mapDlg.value = true
}
async function saveMaps() {
  saving.value = true
  try {
    await api.post('/td-interfaces/' + mapCode.value + '/mappings', { items: mapForm.value.filter(m => m.md_code) })
    mapDlg.value = false; ElMessage.success(t('interfaces.saved')); load()
  } catch (e) { ElMessage.error(apiErr(e)) } finally { saving.value = false }
}
async function load() {
  try {
    const [list, prov] = await Promise.all([api.get(base()), api.get(base() + '/providers')])
    rows.value = list.items || []
    providers.value = prov.items || []
    if (props.kind !== 'ds') {
      const s = await api.get('/interfaces/status', { params: { kind: props.kind } })
      status.value = Object.fromEntries((s.items || []).map(x => [x.code, x]))
    }
    if (props.kind === 'td') {
      maps.value = {}; mapsOk.value = {}
      await Promise.all(rows.value.map(async r => {
        try { maps.value[r.code] = (await api.get('/td-interfaces/' + r.code + '/mappings')).items || []; mapsOk.value[r.code] = true }
        catch { maps.value[r.code] = []; mapsOk.value[r.code] = false }
      }))
      mdRows.value = (await api.get('/md-interfaces')).items || []
    }
    if (props.kind === 'md') {
      refs.value = {}
      await Promise.all(rows.value.map(async r => {
        try { refs.value[r.code] = (await api.get('/md-interfaces/' + r.code + '/mappings')).items || [] }
        catch { refs.value[r.code] = [] }
      }))
    }
    emit('loaded', rows.value)
  } catch (e) { ElMessage.error(apiErr(e)); rows.value = [] }
}
onMounted(async () => {
  try { perms.value = (await meOnce())?.permissions || [] } catch { perms.value = [] }
  load()
})
</script>

<style scoped>
.hd { display: flex; align-items: center; justify-content: space-between; }
.hd-title { font-size: var(--fs-card); font-weight: 600; }
.hd-acts { display: flex; align-items: center; gap: var(--icon-gap); }
.code { font-family: var(--font-num); font-size: var(--fs-foot); }
.tag { margin: 0 var(--sp-1) 0 0; }
.tag-v { color: var(--brand-600); font-size: var(--fs-foot); }
.dot { display: inline-block; width: var(--dot); height: var(--dot); border-radius: 50%; margin-right: var(--sp-1); vertical-align: middle; }
.dot.ok { background: var(--success); }
.dot.warn { background: var(--warn-fill); }
.dot.off { background: var(--flat); }
.muted { color: var(--text-secondary); font-size: var(--fs-foot); }
.missing { color: var(--critical); font-size: var(--fs-foot); }
.note { margin: var(--sp-2) 0; }
.opt-tag { float: right; color: var(--text-secondary); font-size: var(--fs-foot); }
.map-foot { margin-top: var(--sp-2); }
</style>
