<template>
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span>{{ t('imBots.title') }}</span>
        <div style="display:flex;gap:8px">
          <el-button v-if="qrSession" type="warning" @click="cancelQr">{{ t('common.cancel') }}</el-button>
          <el-button v-else type="primary" @click="showAdd = true">{{ t('imBots.addBot') }}</el-button>
        </div>
      </div>
    </template>

    <!-- 扫码向导(interactive 平台) -->
    <el-card v-if="qrSession" shadow="hover" style="margin-bottom:16px">
      <template #header>{{ t('imBots.qrTitle') }}</template>
      <div style="display:flex;gap:24px;align-items:center">
        <img v-if="qrData.qr_img" :src="qrData.qr_img" style="width:200px;height:200px" alt="QR" />
        <div>
          <el-tag :type="qrStatusType">{{ qrData.status || 'pending' }}</el-tag>
          <div style="margin-top:8px;color:#909399;font-size:13px">{{ t('imBots.qrHint') }}</div>
        </div>
      </div>
    </el-card>

    <el-table :data="bots">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column :label="t('imBots.providerCol')" width="100">
        <template #default="{ row }">
          <el-tag>{{ $t('imBots.provider.' + row.provider, row.provider) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
      <el-table-column prop="route_key" label="App ID" min-width="150" show-overflow-tooltip />
      <el-table-column :label="t('imBots.defaultRole')" width="100">
        <template #default="{ row }"><el-tag type="info">{{ row.default_role }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.enabled')" width="80">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'warning'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="420">
        <template #default="{ row }">
          <el-button v-if="!row.enabled" type="success" @click="startBot(row.id)">{{ t('common.start') }}</el-button>
          <el-button v-else type="warning" @click="stopBot(row.id)">{{ t('common.stop') }}</el-button>
          <el-button type="primary" @click="openEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button type="primary" @click="testBot(row.id)">{{ t('common.test') }}</el-button>
          <el-button type="primary" @click="openUsers(row)">{{ t('imBots.users') }}</el-button>
          <el-button type="danger" @click="delBot(row.id)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加向导 -->
    <el-dialog v-model="showAdd" :close-on-click-modal="false" :title="t('imBots.addBot')" width="560px">
      <el-form label-width="120px">
        <el-form-item :label="t('imBots.providerCol')">
          <el-select v-model="addForm.provider" style="width:100%" @change="onProviderChange">
            <el-option v-for="p in providers" :key="p.provider" :value="p.provider"
                       :label="$t('imBots.provider.' + p.provider, p.provider)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.name')">
          <el-input v-model="addForm.name" :placeholder="t('imBots.phName')" />
        </el-form-item>
        <template v-if="currentSchema.length">
          <el-form-item v-for="f in currentSchema" :key="f.key" :label="fieldLabel(f)">
            <el-input v-if="f.type === 'textarea'" v-model="addForm.credentials[f.key]" type="textarea" :rows="2" />
            <el-input v-else v-model="addForm.credentials[f.key]" :show-password="!!f.secret"
                      :placeholder="fieldLabel(f)" />
          </el-form-item>
        </template>
        <el-form-item :label="t('imBots.defaultRole')">
          <el-select v-model="addForm.default_role" style="width:100%">
            <el-option v-for="r in ['viewer','analyst','trader']" :key="r" :value="r" :label="r" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAdd = false">{{ t('common.cancel') }}</el-button>
        <el-button v-if="currentProvider?.onboarding === 'interactive'" type="warning" @click="startQr">{{ t('imBots.qrBtn') }}</el-button>
        <el-button type="primary" @click="createBot">{{ t('common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- 编辑(含凭证补录) -->
    <el-dialog v-model="showEdit" :close-on-click-modal="false" :title="t('imBots.editBot')" width="560px">
      <el-form label-width="120px">
        <el-form-item :label="t('common.name')"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item :label="t('common.remark')"><el-input v-model="editForm.description" /></el-form-item>
        <el-form-item :label="t('imBots.defaultRole')">
          <el-select v-model="editForm.default_role" style="width:100%">
            <el-option v-for="r in ['viewer','analyst','trader']" :key="r" :value="r" :label="r" />
          </el-select>
        </el-form-item>
        <el-form-item v-for="f in editSchema" :key="f.key" :label="fieldLabel(f)">
          <el-input v-model="editForm.credentials[f.key]" :show-password="!!f.secret"
                    :placeholder="t('imBots.phKeepBlank')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 用户授权管理 -->
    <el-dialog v-model="showUsers" :close-on-click-modal="false" :title="`${t('imBots.users')} #${usersBotId}`" width="560px">
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <el-input v-model="newUser.im_user_id" placeholder="ou_xxxxxxxx" style="width:280px" />
        <el-select v-model="newUser.role" style="width:130px">
          <el-option v-for="r in ['viewer','analyst','trader']" :key="r" :value="r" :label="r" />
        </el-select>
        <el-button type="primary" @click="addUser">{{ t('common.add') }}</el-button>
      </div>
      <el-table :data="botUsers" max-height="300">
        <el-table-column prop="im_user_id" label="IM User ID" min-width="220" show-overflow-tooltip />
        <el-table-column prop="role" :label="t('imBots.roleCol')" width="110" />
        <el-table-column :label="t('common.action')" width="90">
          <template #default="{ row }">
            <el-button type="danger" @click="delUser(row.im_user_id)">✕</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'

const { t } = useI18n()
const bots = ref([])
const providers = ref([])
const showAdd = ref(false)
const showEdit = ref(false)
const showUsers = ref(false)
const qrSession = ref('')
const qrData = ref({})
const usersBotId = ref(0)
const botUsers = ref([])
const newUser = ref({ im_user_id: '', role: 'viewer' })
const addForm = ref({ provider: 'feishu', name: '', default_role: 'viewer', credentials: {} })
const editForm = ref({ name: '', description: '', default_role: 'viewer', credentials: {} })
const editSchema = ref([])
let qrTimer = null

const currentProvider = computed(() => providers.value.find(p => p.provider === addForm.value.provider))
const currentSchema = computed(() => currentProvider.value?.field_schema || [])

const fieldLabel = f => {
  const g = useI18n().global
  return g.te(f.label_key) ? t(f.label_key) : f.key   // 词条兜底回退字段名(apiErr 同款模式)
}
const qrStatusType = computed(() =>
  ({ done: 'success', error: 'danger', scanning: 'warning' }[qrData.value.status] || 'info'))

const load = async () => {
  try { bots.value = await api.get('/im-bots') } catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
}
const loadProviders = async () => {
  try { providers.value = await api.get('/im-bots/providers') } catch { }
}
const onProviderChange = () => { addForm.value.credentials = {} }

const createBot = async () => {
  try {
    await api.post('/im-bots', addForm.value)
    ElMessage.success(t('common.createSuccess'))
    showAdd.value = false
    addForm.value = { provider: addForm.value.provider, name: '', default_role: 'viewer', credentials: {} }
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.createFailed'))) }
}

const startQr = async () => {
  try {
    const r = await api.post(`/im-bots/onboarding/${addForm.value.provider}`)
    qrSession.value = r.ticket
    qrData.value = {}
    qrTimer = setInterval(pollQr, 3000)
  } catch (e) { ElMessage.error(apiErr(e)) }
}
const pollQr = async () => {
  try {
    const r = await api.get(`/im-bots/onboarding-status/${qrSession.value}`)
    qrData.value = r
    if (r.status === 'done' || r.status === 'error') { cancelQr(); await load() }
  } catch { }
}
const cancelQr = () => { qrSession.value = ''; qrData.value = {}; if (qrTimer) clearInterval(qrTimer) }

const startBot = async id => { try { await api.post(`/im-bots/${id}/start`); await load() } catch (e) { ElMessage.error(apiErr(e)) } }
const stopBot = async id => { try { await api.post(`/im-bots/${id}/stop`); await load() } catch (e) { ElMessage.error(apiErr(e)) } }
const testBot = async id => {
  try {
    const r = await api.post(`/im-bots/${id}/test`)
    r.ok ? ElMessage.success(r.detail || 'OK') : ElMessage.error(r.detail || r.error || 'FAIL')
  } catch (e) { ElMessage.error(apiErr(e)) }
}
const delBot = async id => {
  try { await ElMessageBox.confirm(t('common.confirmDelete'), t('common.tip'), { type: 'warning' }) } catch { return }
  try { await api.delete(`/im-bots/${id}`); await load() } catch (e) { ElMessage.error(apiErr(e)) }
}

const openEdit = row => {
  editForm.value = { name: row.name, description: row.description || '', default_role: row.default_role, credentials: {} }
  editSchema.value = providers.value.find(p => p.provider === row.provider)?.field_schema || []
  editForm.value._id = row.id
  showEdit.value = true
}
const saveEdit = async () => {
  const id = editForm.value._id
  const creds = Object.fromEntries(Object.entries(editForm.value.credentials).filter(([, v]) => v))
  try {
    await api.post(`/im-bots/${id}`, { name: editForm.value.name, description: editForm.value.description, default_role: editForm.value.default_role, credentials: creds })
    ElMessage.success(t('common.saveSuccess'))
    showEdit.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

const openUsers = async row => { usersBotId.value = row.id; showUsers.value = true; await loadUsers() }
const loadUsers = async () => { try { botUsers.value = await api.get(`/im-bots/${usersBotId.value}/users`) } catch { botUsers.value = [] } }
const addUser = async () => {
  try { await api.post(`/im-bots/${usersBotId.value}/users`, newUser.value); newUser.value = { im_user_id: '', role: 'viewer' }; await loadUsers() }
  catch (e) { ElMessage.error(apiErr(e)) }
}
const delUser = async uid => { try { await api.delete(`/im-bots/${usersBotId.value}/users/${encodeURIComponent(uid)}`); await loadUsers() } catch (e) { ElMessage.error(apiErr(e)) } }

onMounted(() => { load(); loadProviders() })
onUnmounted(() => { if (qrTimer) clearInterval(qrTimer) })
</script>
