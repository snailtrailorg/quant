<template>
  <!-- D25:配置面卡片(kind 参数化——83a 拆表后「数据源」「交易账号」各一页签各一实例)。
       立法(27 号/D25 + 批 83a):行=账号/列=能力集;域=表=端点族(卡片按 kind 打自己那族的端点)。
       筛选态禁拖(§九——规避 reorder 全量校验 400);交易族行不可拖(批 66a:D26 §4.3 通道切换
       语义随 M5/hub 绑行消失——曾按 'trading' 能力判定,拆表后由 kind 直接决定)。
       数据源族无 exchanges/account_key(83a 列集立法——该两列不渲染)。 -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-weight: 600">{{ t(titleKey) }}</span>
        <div style="display:flex; gap:8px; align-items:center">
          <el-select v-model="capFilter" clearable size="small" style="width: 130px"
                     :placeholder="t('interfaces.capFilterPh')">
            <el-option v-for="c in capTokens" :key="c" :value="c" :label="t(`interfaces.cap_${c}`)" />
          </el-select>
          <ColumnSettings :storage-key="'cols.interfaces.' + kind" :columns="colDefs" v-model:visible="visible" />
          <IconBtn :icon="Plus" :title="t('common.create')" @click="openAdd" />
        </div>
      </div>
    </template>
    <!-- 83a：两族列集不同（交易族多 exchanges/account_key 两列），列宽/列显隐按 kind 各存一份 -->
    <ChannelTableShell ref="shellRef" :rows="rows" :storage-key="'interfaces.' + kind"
                       :drag-title-key="'interfaces.dragTitle'" :note-key="'interfaces.orderNote'"
                       :can-drag="capFilter ? () => false : () => canDragRow"
                       :reorder="doReorder" @reorder-failed="onReorderFailed">
      <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip>
        <template #default="{ row, $index }">
          <span @dragover.prevent @drop="shellRef?.onDrop($index)">{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="provider" label="Provider" min-width="120" />
      <el-table-column prop="market" :label="t('interfaces.colMarket')" min-width="90">
        <template #default="{ row }">{{ t(row.market === 'astock' ? 'interfaces.marketAstock' : 'interfaces.marketCrypto') }}</template>
      </el-table-column>
      <el-table-column :label="t('interfaces.colCapabilities')" min-width="200">
        <template #default="{ row }">
          <el-tag v-for="c in row.capabilities" :key="c" size="small" style="margin: 1px"
                  :type="c === 'trading' ? 'warning' : 'info'">{{ t(`interfaces.cap_${c}`) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="showExchanges && colOn('exchanges')" :label="t('interfaces.colExchanges')" min-width="140">
        <template #default="{ row }">
          <span style="font-size: var(--fs-foot)">{{ (row.exchanges && row.exchanges.length) ? row.exchanges.join(' / ') : t('interfaces.allExchanges') }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="showExchanges" prop="account_key" :label="t('interfaces.colAccountKey')" min-width="150">
        <template #default="{ row }">
          <code style="font-family: var(--font-num); font-size: var(--fs-foot)">{{ row.account_key || '—' }}</code>
        </template>
      </el-table-column>
      <el-table-column prop="has_credentials" :label="t('common.credential')" min-width="120">
        <template #default="{ row }"><el-tag :type="row.has_credentials ? 'success' : 'info'">{{ row.has_credentials ? t('common.configured') : t('common.notConfigured') }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="enabled" :label="t('common.enable')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column v-if="colOn('updated_at')" prop="updated_at" :label="t('common.updatedAt')" min-width="220">
        <template #default="{ row }">{{ row.updated_at ? fmtTime.full(row.updated_at) : '-' }}</template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="150">
        <template #default="{ row }">
          <IconBtn size="small" :icon="VideoPlay" :title="t('common.test')" @click="onTest(row.id)" :loading="testing === row.id" />
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')" @click="onDelete(row)" />
        </template>
      </el-table-column>
    </ChannelTableShell>

    <el-dialog v-model="dlg" :close-on-click-modal="false"
               :title="form.id ? t('interfaces.editTitle') : t('interfaces.addTitle')" width="600px">
      <el-tabs v-if="!form.id" v-model="form.provider" @tab-change="onProviderPick">
        <el-tab-pane v-for="p in providers" :key="p.provider" :name="p.provider"
                     :label="providerLabel(p.provider)" />
      </el-tabs>
      <el-form :model="form" label-width="130px">
        <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
        <el-form-item v-if="form.id" :label="t('interfaces.providerLabel')">
          <el-input :model-value="providerLabel(form.provider)" disabled />
        </el-form-item>
        <el-form-item :label="t('interfaces.marketLabel')">
          <el-input :model-value="marketName" disabled />
        </el-form-item>
        <el-form-item :label="t('interfaces.capsLabel')">
          <el-checkbox-group v-model="form.capabilities">
            <el-checkbox v-for="c in availableCaps" :key="c" :value="c">{{ t(`interfaces.cap_${c}`) }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-if="showExchanges" :label="t('interfaces.exchangesLabel')">
          <div>
            <el-select v-model="form.exchanges" multiple style="width: 100%" :placeholder="t('interfaces.allExchanges')">
              <el-option v-for="e in marketExchanges" :key="e" :value="e" :label="e" />
            </el-select>
            <div class="exch-note">{{ t('interfaces.exchangesNote') }}</div>
          </div>
        </el-form-item>
        <el-form-item v-if="showExchanges" :label="t('interfaces.accountKeyLabel')">
          <el-input v-model="form.account_key" :placeholder="t('interfaces.accountKeyPh')" />
        </el-form-item>
        <template v-for="f in curFieldSchema" :key="f.key">
          <el-form-item :label="schemaLabel(f)">
            <el-input v-if="f.type === 'textarea'" v-model="form.creds[f.key]" type="textarea" />
            <el-input v-else-if="f.type === 'number'" v-model.number="form.creds[f.key]" />
            <el-switch v-else-if="f.type === 'boolean'" v-model="form.creds[f.key]" />
            <el-select v-else-if="f.type === 'select'" v-model="form.creds[f.key]" style="width: 100%">
              <el-option v-for="o in (f.options || [])" :key="o" :value="o" :label="f.option_label_key ? t(f.option_label_key) : o" />
            </el-select>
            <el-input v-else v-model="form.creds[f.key]" :type="f.secret ? 'password' : 'text'"
                      show-password autocomplete="new-password" />
          </el-form-item>
        </template>
        <template v-for="f in curParamsSchema" :key="'p' + f.key">
          <el-form-item :label="schemaLabel(f)">
            <el-input v-if="f.type === 'textarea'" v-model="form.params[f.key]" type="textarea" />
            <el-input v-else-if="f.type === 'number'" v-model.number="form.params[f.key]" />
            <el-switch v-else-if="f.type === 'boolean'" v-model="form.params[f.key]" />
            <el-select v-else-if="f.type === 'select'" v-model="form.params[f.key]" style="width: 100%">
              <el-option v-for="o in (f.options || [])" :key="o" :value="o" :label="f.option_label_key ? t(f.option_label_key) : o" />
            </el-select>
            <el-input v-else v-model="form.params[f.key]" />
          </el-form-item>
        </template>
        <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ChannelTableShell from './ChannelTableShell.vue'
import ColumnSettings from './ColumnSettings.vue'
import IconBtn from './IconBtn.vue'
import { Plus, VideoPlay, Edit, Delete } from '@element-plus/icons-vue'
import { fmtTime } from '../utils/fmtTime'
import { apiErr, getInterfaces, getInterfaceProviders, createInterface, updateInterface,
         deleteInterface, testInterface, reorderInterfaces } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps({
  // 批 83a：kind=域=表（data_source | trading_account）——与后端 markets.DOMAIN_* 同源
  kind: { type: String, required: true },
})
const emit = defineEmits(['loaded'])   // 批55b 盲审 A-P1-2：CRUD 后通知父组件（限流面板显隐联动）
const { t, te } = useI18n()

// 批 83a：两族端点同构，差异只有「能力集值域 / exchanges+account_key 两列 / 可否拖」
const isTrading = computed(() => props.kind === 'trading_account')
const titleKey = computed(() => isTrading.value ? 'interfaces.cardTitleTrading' : 'interfaces.cardTitleData')
const showExchanges = computed(() => isTrading.value)
// 批 66a/D26 §4.3：交易族行不可拖（通道切换语义随 M5/hub 绑行消失）；数据源族保留拖拽
const canDragRow = computed(() => !isTrading.value)

const rows = ref([])
const providers = ref([])
const domainCaps = ref([])     // 本域能力集（后端返回，前端零字面量）
const dlg = ref(false)
const saving = ref(false)
const testing = ref(0)
const shellRef = ref(null)
const form = ref(emptyForm())
const visible = ref([])
const capFilter = ref('')   // D25 §九：能力筛选；筛选态禁拖（全量校验防 400）
const capTokens = computed(() => domainCaps.value)
const colDefs = computed(() => [
  // 83a：exchanges 列仅交易族有（数据源族列集不含该列——不出列显隐项，防幻影开关）
  ...(showExchanges.value ? [{ key: 'exchanges', label: t('interfaces.colExchanges') }] : []),
  { key: 'updated_at', label: t('common.updatedAt'), hidden: true },
])
const colOn = k => visible.value.includes(k)

const applyFilter = () => {
  rows.value = capFilter.value
    ? allRows.value.filter(r => (r.capabilities || []).includes(capFilter.value))
    : allRows.value
}

const allRows = ref([])   // 全量缓存
const load = async () => {
  try {
    allRows.value = await getInterfaces(props.kind)
    applyFilter()
    emit('loaded', allRows.value)
  } catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
}
const loadProviders = async () => {
  try {
    const res = await getInterfaceProviders(props.kind) || {}
    providers.value = res.providers || []
    domainCaps.value = res.domain_capabilities || []
  } catch (e) {
    providers.value = []
    domainCaps.value = []
    console.error('providers 目录加载失败', e)   // 静默退化：新建弹窗空下拉+保存报后端错（盲审 A-P2-7）
  }
}
watch(capFilter, applyFilter)
onMounted(() => { load(); loadProviders() })

function emptyForm() {
  return { id: null, name: '', provider: '', market: '', exchanges: [], account_key: '',
           capabilities: [], creds: {}, params: {}, enabled: true, _code_caps: [] }
}

const marketExchanges = computed(() =>
  providers.value.find(p => p.provider === form.value.provider)?.market_exchanges || [])
// 批63：凭证/参数字段 schema（据选定 provider 动态取；编辑态凭证留空=不改，密文不可回显）
const curMeta = computed(() => providers.value.find(p => p.provider === form.value.provider) || {})
const curFieldSchema = computed(() => curMeta.value.field_schema || [])
const curParamsSchema = computed(() => curMeta.value.params_schema || [])
const schemaLabel = f => (f.label_key && te(f.label_key)) ? t(f.label_key) : f.key
// 批63：provider 页签 label——中文名优先（interfaces.provider.* 词条），缺失回退 provider 技术名
const providerLabel = p => te('interfaces.provider.' + p) ? t('interfaces.provider.' + p) : p
// 并集：漂移能力（配置含代码外项）也显示为可勾选项——用户可取消勾掉修复漂移（盲审 A-P2-5）；
// 83a：再 ∩ 本域能力集并按域序排列（后端写侧同口径——域外项勾了必 400）
const availableCaps = computed(() => {
  const pool = new Set([...(form.value._code_caps || []), ...(form.value.capabilities || [])])
  return domainCaps.value.filter(c => pool.has(c))
})
const marketName = computed(() => form.value.market === 'astock' ? t('interfaces.marketAstock')
                           : form.value.market === 'crypto' ? t('interfaces.marketCrypto') : form.value.market)

const onProviderPick = (p) => {
  const meta = providers.value.find(x => x.provider === p)
  if (!meta) return
  form.value.market = meta.market
  form.value._code_caps = meta.capabilities
  form.value.capabilities = [...meta.capabilities]   // 新建默认全启用（可收窄）
  form.value.exchanges = [...(meta.default_exchanges || [])]   // perp 预填单所——所见即所得（盲审 B-P1-3）
  form.value.creds = {}   // 批63：schema 随 provider 变，凭证/参数收集重置
  form.value.params = {}
}

const openAdd = () => {
  form.value = emptyForm()
  if (providers.value.length) onProviderPick(providers.value[0].provider)
  dlg.value = true
}
const openEdit = (row) => {
  form.value = { ...row, creds: {}, params: row.params || {}, exchanges: row.exchanges || [],
                 account_key: row.account_key || '',
                 _code_caps: row.code_capabilities || row.capabilities }
  dlg.value = true
}

const onSave = async () => {
  saving.value = true
  try {
    const credsFilled = Object.values(form.value.creds || {}).some(v => v !== '' && v !== null && v !== undefined)
    const payload = { name: form.value.name, provider: form.value.provider, market: form.value.market,
                      capabilities: form.value.capabilities,
                      credentials: credsFilled ? JSON.stringify(form.value.creds) : '',   // 批63：全空=不改（编辑态密文不可回显）
                      params: form.value.params || {}, enabled: form.value.enabled }
    if (showExchanges.value) {
      // 交易族专属两列（数据源族无此语义——83a 列集立法，不发这两键）
      payload.exchanges = form.value.exchanges && form.value.exchanges.length ? form.value.exchanges : null
      payload.account_key = form.value.account_key || null
    }
    if (form.value.id) await updateInterface(props.kind, form.value.id, payload)
    else await createInterface(props.kind, payload)
    ElMessage.success(t('common.saveSuccess'))
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (row) => {
  try {
    await ElMessageBox.confirm(t('interfaces.confirmDelete', { name: row.name }), t('common.tip'), { type: 'warning' })
  } catch { return }
  try {
    await deleteInterface(props.kind, row.id)
    ElMessage.success(t('common.deleteSuccess'))
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

const onTest = async (id) => {
  testing.value = id
  try {
    const r = await testInterface(props.kind, id)
    if (r.ok) ElMessage.success(t('common.connectSuccess'))
    else ElMessage.error(t('common.failedPrefix') + r.error)
  } catch (e) { ElMessage.error(apiErr(e, t('common.testFailed'))) }
  finally { testing.value = 0 }
}

const doReorder = async (ids) => { await reorderInterfaces(props.kind, ids) }
const onReorderFailed = async (e) => {
  ElMessage.error(apiErr(e, t('common.saveFailed')))
  await load()
}
</script>

<style scoped>
.exch-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-top: 4px; }
</style>
