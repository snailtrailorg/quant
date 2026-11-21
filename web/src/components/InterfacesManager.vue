<template>
  <div>
    <el-radio-group v-model="kind" @change="load" class="mb">
      <el-radio-button value="td">交易接口 (TD)</el-radio-button>
      <el-radio-button value="md">行情接口 (MD)</el-radio-button>
    </el-radio-group>
    <div v-if="canWrite" class="mb">
      <el-button type="primary" size="small" @click="openCreate">新增{{ kind === 'td' ? '交易' : '行情' }}接口</el-button>
    </div>
    <el-table :data="rows" row-key="code" size="small" border>
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="pad">
            <b>取价映射（只读）</b>
            <span v-if="!(maps[row.code] && maps[row.code].length)" class="muted"> —（无映射）</span>
            <el-tag v-for="m in (maps[row.code] || [])" :key="(kind === 'td' ? m.md_code : m.td_code) + '-' + m.priority" size="small" class="mtag">
              {{ kind === 'td' ? m.md_code : m.td_code }} · p{{ m.priority }}
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="code" label="code" min-width="110" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="provider" label="provider" min-width="100" />
      <el-table-column prop="market" label="市场" min-width="80" />
      <el-table-column prop="impl" label="impl" min-width="70" />
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

    <el-dialog v-model="dlg" :title="(form._create ? '新增' : '编辑') + '接口'" :width="560">
      <el-form label-width="90px">
        <el-form-item label="code">
          <el-input v-if="form._create" v-model="form.code" :placeholder="kind === 'td' ? 'td-xxx（小写字母数字-，不可改）' : 'md-xxx'" />
          <el-input v-else :model-value="form.code" disabled />
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="provider"><el-input v-model="form.provider" :disabled="!form._create" /></el-form-item>
        <el-form-item label="市场"><el-input v-model="form.market" :disabled="!form._create" /></el-form-item>
        <el-form-item v-if="kind === 'td'" label="account_key"><el-input v-model="form.account_key" placeholder="物理资金账号（凭证内优先）" /></el-form-item>
        <el-form-item label="凭证 JSON"><el-input v-model="form.credentials" type="textarea" :rows="3" placeholder='{"app_id":...,"app_secret":...}（留空不改）' /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api, { apiErr, meOnce } from '../api'

const kind = ref('td')
const rows = ref([])
const maps = ref({})
const perms = ref([])
const dlg = ref(false)
const form = ref({})

const canWrite = computed(() => perms.value.includes('system_config'))
const base = k => (k === 'td' ? '/td-interfaces' : '/md-interfaces')

async function load() {
  try {
    const r = await api.get(base(kind.value))
    rows.value = r.items || []
    maps.value = {}
    for (const row of rows.value) {
      try { maps.value[row.code] = (await api.get(base(kind.value) + '/' + row.code + '/mappings')).items || [] }
      catch { maps.value[row.code] = [] }
    }
  } catch (e) { ElMessage.error(apiErr(e)); rows.value = [] }
}
function openCreate() {
  form.value = { _create: true, code: '', name: '', provider: '', market: 'astock', account_key: '', credentials: '' }
  dlg.value = true
}
function openEdit(row) {
  form.value = { _create: false, code: row.code, name: row.name, provider: row.provider, market: row.market,
                 account_key: row.account_key || '', credentials: '' }
  dlg.value = true
}
async function save() {
  const p = { name: form.value.name }
  if (form.value._create) { p.code = form.value.code; p.provider = form.value.provider; p.market = form.value.market }
  if (kind.value === 'td' && form.value.account_key !== undefined) p.account_key = form.value.account_key
  if (form.value.credentials && form.value.credentials.trim()) p.credentials_encrypted = form.value.credentials.trim()
  try {
    if (form.value._create) await api.post(base(kind.value), p)
    else await api.patch(base(kind.value) + '/' + form.value.code, p)
    dlg.value = false; ElMessage.success('已保存'); load()
  } catch (e) { ElMessage.error(apiErr(e)) }
}
async function toggle(row, v) {
  try { await api.post(base(kind.value) + '/' + row.code + '/enable', { enabled: v }); row.enabled = v }
  catch (e) { ElMessage.error(apiErr(e)); load() }
}
async function del(row) {
  try { await api.delete(base(kind.value) + '/' + row.code); ElMessage.success('已删除'); load() }
  catch (e) { ElMessage.error(apiErr(e)) }
}
onMounted(async () => { try { perms.value = (await meOnce()).perms || [] } catch { perms.value = [] } load() })
</script>

<style scoped>
.mb { margin-bottom: var(--sp-3); }
.pad { padding: var(--sp-2) var(--sp-3); }
.muted { color: var(--text-3); }
.mtag { margin-left: var(--sp-1); }
</style>
