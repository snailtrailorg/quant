<template>
  <div>
    <div v-if="canWrite" class="mb">
      <el-button type="primary" size="small" @click="openCreate">新增{{ labelOf(kind) }}</el-button>
    </div>
    <el-table :data="rows" row-key="code" size="small" border>
      <el-table-column v-if="kind !== 'ds'" type="expand">
        <template #default="{ row }">
          <div class="pad">
            <b>取价映射（只读）</b>
            <span v-if="!(maps[row.code] && maps[row.code].length)" class="muted"> —（无映射）</span>
            <el-tag v-for="m in (maps[row.code] || [])" :key="(kind === 'td' ? m.md_code : m.td_code) + '-' + m.priority" size="small" class="mtag">
              {{ kind === 'td' ? m.md_code : m.td_code }} · p{{ m.priority }}
            </el-tag>
            <el-button v-if="kind === 'td' && canWrite" link type="primary" size="small" class="mtag" :disabled="mapsOk[row.code] !== true" @click="openMaps(row)">编辑映射</el-button>
            <span v-if="mapsOk[row.code] !== true" class="muted">（映射未就绪，暂不可编辑）</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="code" label="code" min-width="110" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="provider" label="provider" min-width="100" />
      <el-table-column prop="market" label="市场" min-width="80" />
      <el-table-column v-if="kind === 'ds'" label="能力" min-width="180">
        <template #default="{ row }">{{ (row.capabilities || []).join(', ') }}</template>
      </el-table-column>
      <el-table-column v-else prop="impl" label="impl" min-width="70" />
      <el-table-column v-if="kind !== 'ds'" label="载体" min-width="90">
        <template #default="{ row }"><span :class="carrierOf(row.provider) === '就绪' ? 'on' : 'off'">{{ carrierOf(row.provider) }}</span></template>
      </el-table-column>
      <el-table-column label="启用" min-width="80">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" :disabled="!canWrite" @change="v => toggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column label="凭证" min-width="70">
        <template #default="{ row }"><span>{{ row.has_credentials ? '有' : '无' }}</span></template>
      </el-table-column>
      <el-table-column v-if="canWrite" label="操作" min-width="140">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-popconfirm title="确认删除该接口？" @confirm="del(row)">
            <template #reference><el-button link type="danger" size="small">删除</el-button></template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="(form._create ? '新增' : '编辑') + labelOf(kind)" :width="560">
      <el-form label-width="100px">
        <el-form-item label="code">
          <el-input v-if="form._create" v-model="form.code" :placeholder="prefixOf(kind) + 'xxx（小写字母数字-，不可改）'" />
          <el-input v-else :model-value="form.code" disabled />
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="provider">
          <el-select v-if="providers.length" v-model="form.provider" :disabled="!form._create" filterable allow-create>
            <el-option v-for="x in providers" :key="x.provider" :label="x.provider" :value="x.provider" />
          </el-select>
          <el-input v-else v-model="form.provider" :disabled="!form._create" placeholder="ds-* 由数据源注册表提供" />
        </el-form-item>
        <el-form-item label="市场"><el-input v-model="form.market" :disabled="!form._create" /></el-form-item>
        <el-form-item v-if="kind === 'ds'" label="能力">
          <el-input v-model="form.capabilities" placeholder="逗号分隔，如 hist_quote,ref_data" />
        </el-form-item>
        <el-form-item v-if="kind === 'td'" label="account_key">
          <el-input v-model="form.account_key" placeholder="物理资金账号（凭证内优先）" />
        </el-form-item>
        <el-form-item v-if="kind === 'td'" label="虚拟账户">
          <el-switch v-model="form.is_virtual" :disabled="!form._create" />
        </el-form-item>
        <el-form-item label="凭证 JSON">
          <el-input v-model="form.credentials" type="textarea" :rows="3" placeholder='{"app_id":...,"app_secret":...}（留空不改）' />
        </el-form-item>
        <el-form-item v-if="kind !== 'ds'" label="参数 JSON">
          <el-input v-model="form.params" type="textarea" :rows="3" placeholder='{"td_host":"host:port"}（留空不改）' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mapDlg" :title="'编辑取价映射 · ' + mapCode" :width="520">
      <el-table :data="mapForm" size="small" border>
        <el-table-column label="行情接口" min-width="200">
          <template #default="{ row }">
            <el-select v-model="row.md_code" filterable placeholder="md-*">
              <el-option v-for="m in mdOf" :key="m.code" :label="m.code" :value="m.code" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="优先级" min-width="110">
          <template #default="{ row }"><el-input-number v-model="row.priority" :min="1" :max="99" size="small" /></template>
        </el-table-column>
        <el-table-column label="" min-width="60">
          <template #default="{ $index }">
            <el-button link type="danger" size="small" @click="mapForm.splice($index, 1)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button size="small" class="mtag" @click="mapForm.push({ md_code: '', priority: mapForm.length + 1 })">添加</el-button>
      <div class="muted mtag">保存后进程在 2–45s 内检测并重启生效（主备顺序=priority）。</div>
      <template #footer>
        <el-button @click="mapDlg = false">取消</el-button>
        <el-button type="primary" @click="saveMaps">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api, { apiErr, meOnce } from '../api'

const props = defineProps({ kind: { type: String, required: true } })
const emit = defineEmits(['loaded'])
const rows = ref([])
const maps = ref({})
const providers = ref([])
const perms = ref([])
const dlg = ref(false)
const form = ref({})
const mapDlg = ref(false)
const mapCode = ref('')
const mapForm = ref([])
const mapMarket = ref('')
const mdOptions = ref([])
const mapsOk = ref({})
const mdOf = computed(() => mdOptions.value.filter(m => !mapMarket.value || m.market === mapMarket.value))

const canWrite = computed(() => perms.value.includes('system_config'))
const base = k => (k === 'td' ? '/td-interfaces' : k === 'md' ? '/md-interfaces' : '/data-sources')
const labelOf = k => (k === 'td' ? '交易接口' : k === 'md' ? '行情接口' : '数据源')
const prefixOf = k => (k === 'td' ? 'td-' : k === 'md' ? 'md-' : 'ds-')
const carrierOf = p => {
  const x = providers.value.find(v => v.provider === p)
  return !x ? '未知' : (x.ready ? '就绪' : '待接入')
}

async function load() {
  try {
    const r = await api.get(base(props.kind))
    rows.value = r.items || []
    maps.value = {}
    if (props.kind !== 'ds') {
      mapsOk.value = {}
      for (const row of rows.value) {
        try {
          maps.value[row.code] = (await api.get(base(props.kind) + '/' + row.code + '/mappings')).items || []
          mapsOk.value[row.code] = true
        } catch {
          maps.value[row.code] = []
          mapsOk.value[row.code] = false   // 读失败≠无映射：禁编辑，防全量替换误清空
        }
      }
    }
    emit('loaded', rows.value)
  } catch (e) { ElMessage.error(apiErr(e)); rows.value = [] }
}
async function loadProviders() {
  if (props.kind === 'ds') return
  try { providers.value = (await api.get(base(props.kind) + '/providers')).items || [] } catch { providers.value = [] }
  if (props.kind === 'td') {
    try { mdOptions.value = (await api.get('/md-interfaces')).items || [] } catch { mdOptions.value = [] }
  }
}
function openMaps(row) {
  mapCode.value = row.code
  mapMarket.value = row.market
  mapForm.value = (maps.value[row.code] || []).map(m => ({ md_code: m.md_code, priority: m.priority }))
  mapDlg.value = true
}
async function saveMaps() {
  const items = mapForm.value.filter(m => m.md_code)
    .map(m => ({ md_code: m.md_code, priority: Number(m.priority) || 1 }))
  try {
    await api.post('/td-interfaces/' + mapCode.value + '/mappings', { items })
    mapDlg.value = false; ElMessage.success('映射已保存'); load()
  } catch (e) { ElMessage.error(apiErr(e)) }
}
function openCreate() {
  form.value = { _create: true, code: '', name: '', provider: '', market: 'astock',
                 account_key: '', is_virtual: false, capabilities: '', credentials: '', params: '' }
  dlg.value = true
}
function openEdit(row) {
  form.value = { _create: false, code: row.code, name: row.name, provider: row.provider, market: row.market,
                 account_key: row.account_key || '', is_virtual: !!row.is_virtual,
                 capabilities: (row.capabilities || []).join(','),
                 credentials: '', params: JSON.stringify(row.params || {}, null, 0) }
  dlg.value = true
}
async function save() {
  const p = { name: form.value.name }
  if (form.value._create) { p.code = form.value.code; p.provider = form.value.provider; p.market = form.value.market }
  if (props.kind === 'ds') p.capabilities = form.value.capabilities.split(',').map(s => s.trim()).filter(Boolean)
  if (props.kind === 'td') {
    p.account_key = form.value.account_key
    if (form.value._create) p.is_virtual = form.value.is_virtual
  }
  if (form.value.credentials && form.value.credentials.trim()) p.credentials = form.value.credentials.trim()
  if (props.kind !== 'ds' && form.value.params && form.value.params.trim()) {
    try { p.params = JSON.parse(form.value.params) } catch { ElMessage.error('参数不是合法 JSON'); return }
  }
  try {
    if (form.value._create) await api.post(base(props.kind), p)
    else await api.patch(base(props.kind) + '/' + form.value.code, p)
    dlg.value = false; ElMessage.success('已保存'); load()
  } catch (e) { ElMessage.error(apiErr(e)) }
}
async function toggle(row, v) {
  try {
    if (props.kind === 'ds') await api.patch(base(props.kind) + '/' + row.code, { enabled: v })
    else await api.post(base(props.kind) + '/' + row.code + '/enable', { enabled: v })
    row.enabled = v
  } catch (e) { ElMessage.error(apiErr(e)); load() }
}
async function del(row) {
  try { await api.delete(base(props.kind) + '/' + row.code); ElMessage.success('已删除'); load() }
  catch (e) { ElMessage.error(apiErr(e)) }
}
onMounted(async () => {
  try { perms.value = (await meOnce())?.permissions || [] } catch { perms.value = [] }
  loadProviders(); load()
})
</script>

<style scoped>
.mb { margin-bottom: var(--sp-3); }
.pad { padding: var(--sp-2) var(--sp-3); }
.muted { color: var(--text-3); }
.mtag { margin-left: var(--sp-1); }
.on { color: var(--success); }
.off { color: var(--text-3); }
</style>
