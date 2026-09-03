<template>
  <!-- W4（web-design 10 §4 三维矩阵）：角色基线 × 用户 override 双模式三 tab 编辑 -->
  <el-card>
    <template #header>{{ t('perm.title') }}</template>
    <el-alert type="info" :closable="false" style="margin-bottom: 14px">{{ t('perm.note') }}</el-alert>

    <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 14px">
      <el-radio-group v-model="mode">
        <el-radio-button value="role">{{ t('perm.modeRole') }}</el-radio-button>
        <el-radio-button value="user">{{ t('perm.modeUser') }}</el-radio-button>
      </el-radio-group>
      <el-select v-if="mode === 'role'" v-model="role" style="width: 160px">
        <el-option v-for="r in ['viewer','analyst','trader','admin']" :key="r" :value="r" :label="r" />
      </el-select>
      <el-select v-else v-model="userSel" :placeholder="t('perm.pickUser')" style="width: 200px" filterable>
        <el-option v-for="u in users" :key="u.username" :value="u.username" :label="`${u.username} (${u.role})`" />
      </el-select>
    </div>

    <!-- ═══ 角色基线模式：三 tab ═══ -->
    <template v-if="mode === 'role'">
      <el-tabs v-model="tab">
        <el-tab-pane name="api">
          <template #label><b>{{ t('perm.tabApi') }}</b></template>
          <el-checkbox-group v-model="apiSel[role]">
            <el-checkbox v-for="k in keys" :key="k" :value="k" :disabled="locked.includes(k)" style="margin: 6px 14px">
              {{ k }}<span v-if="locked.includes(k)"> 🔒</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-tab-pane>
        <el-tab-pane name="nav">
          <template #label><b>{{ t('perm.tabNav') }}</b></template>
          <el-table :data="navItems" size="small">
            <el-table-column prop="id" :label="t('common.name')" width="140" />
            <el-table-column prop="group" :label="t('perm.navGroup')" width="110" />
            <el-table-column :label="t('perm.navState')">
              <template #default="{ row }">
                <el-radio-group :model-value="navSel[role]?.[row.id] || ''" size="small"
                                @update:model-value="v => setNav(row.id, v)">
                  <el-radio-button value="">—</el-radio-button>
                  <el-radio-button value="hidden">{{ t('perm.navHidden') }}</el-radio-button>
                  <el-radio-button value="readonly">{{ t('perm.navReadonly') }}</el-radio-button>
                  <el-radio-button value="readwrite">{{ t('perm.navReadwrite') }}</el-radio-button>
                </el-radio-group>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane name="data">
          <template #label><b>{{ t('perm.tabData') }}</b></template>
          <div style="margin-bottom: 10px; font-weight: 600">{{ t('perm.dataMarkets') }}</div>
          <el-checkbox-group v-model="dataMarketSel[role]">
            <el-checkbox v-for="m in dataFields.markets" :key="m" :value="m" style="margin: 6px 14px">{{ m }}</el-checkbox>
          </el-checkbox-group>
          <div style="margin: 12px 0 6px; font-weight: 600">{{ t('perm.dataSens') }}</div>
          <el-radio-group :model-value="dataSensSel[role] || ''" @update:model-value="v => dataSensSel[role] = v">
            <el-radio-button value="detail">{{ t('perm.sensDetail') }}</el-radio-button>
            <el-radio-button value="aggregated">{{ t('perm.sensAgg') }}</el-radio-button>
            <el-radio-button value="count">{{ t('perm.sensCount') }}</el-radio-button>
          </el-radio-group>
          <div style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">{{ t('perm.dataNote') }}</div>
          <div style="color: var(--text-secondary); font-size: 12px; margin-top: 4px">{{ t('perm.dualTrackNote') }}</div>
        </el-tab-pane>
      </el-tabs>
      <el-button type="primary" @click="saveRole" :loading="saving" style="margin-top: 14px">{{ t('common.save') }}</el-button>
    </template>

    <!-- ═══ 用户 override 模式 ═══ -->
    <template v-else>
      <template v-if="!userSel">
        <div style="color: var(--text-secondary); padding: 20px 0">{{ t('perm.pickUser') }}</div>
      </template>
      <template v-else>
        <el-alert type="warning" :closable="false" style="margin-bottom: 12px">{{ t('perm.overrideNote') }}</el-alert>
        <el-table :data="userOverrides(userSel)" size="small">
          <el-table-column prop="dimension" :label="t('perm.dim')" width="100" />
          <el-table-column prop="resource" :label="t('perm.resource')" show-overflow-tooltip />
          <el-table-column prop="effect" :label="t('perm.effect')" width="90">
            <template #default="{ row }">
              <el-tag :type="row.effect === 'deny' ? 'danger' : 'success'" size="small">{{ row.effect }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="90">
            <template #default="{ row }">
              <el-button size="small" type="danger" @click="clearOverride(row)">{{ t('perm.clear') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="display: flex; gap: 8px; margin-top: 14px; align-items: center">
          <el-select v-model="newOv.dimension" style="width: 100px">
            <el-option v-for="d in ['api','nav','data']" :key="d" :value="d" :label="d" />
          </el-select>
          <el-input v-model="newOv.resource" :placeholder="t('perm.resource')" style="width: 220px" />
          <el-radio-group v-model="newOv.effect">
            <el-radio-button value="allow">allow</el-radio-button>
            <el-radio-button value="deny">deny</el-radio-button>
          </el-radio-group>
          <el-button type="primary" @click="addOverride">{{ t('perm.addOverride') }}</el-button>
        </div>
        <div v-if="locked.length" style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">
          {{ t('perm.lockedNote') }}: {{ locked.join(' / ') }}
        </div>
      </template>
    </template>
  </el-card>
</template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
const { t } = useI18n()

const mode = ref('role')
const role = ref('viewer')
const userSel = ref('')
const tab = ref('api')
const saving = ref(false)
const keys = ref([])
const locked = ['user_mgmt', 'resume', 'account_keys']
const navItems = ref([])
const dataFields = reactive({ markets: [], sensitivity: [] })
const apiSel = reactive({ viewer: [], analyst: [], trader: [], admin: [] })
const navSel = reactive({})
const dataMarketSel = reactive({ viewer: [], analyst: [], trader: [], admin: [] })
const dataSensSel = reactive({})
const users = ref([])
const overrides = ref([])

const userOverrides = u => overrides.value.filter(o => o.username === u)

const setNav = (id, v) => { (navSel[role.value] = navSel[role.value] || {})[id] = v }

const load = async () => {
  try {
    const r = await api.get('/permissions')
    keys.value = r.keys || []
    Object.assign(apiSel, r.roles || {})
    navItems.value = (r.nav?.items) || []
    for (const [rname, m] of Object.entries(r.nav?.roles || {})) navSel[rname] = { ...m }
    dataFields.markets = r.data?.fields?.markets || []
    dataFields.sensitivity = r.data?.fields?.sensitivity || []
    for (const [rname, m] of Object.entries(r.data?.roles || {})) {
      dataMarketSel[rname] = Object.entries(m)
        .filter(([k, e]) => e === 'allow' && !k.startsWith('sensitivity:')).map(([k]) => k)
      const sensKey = Object.keys(m).find(k => k.startsWith('sensitivity:'))
      dataSensSel[rname] = sensKey ? sensKey.slice('sensitivity:'.length) : ''
    }
    overrides.value = r.user_overrides || []
  } catch { ElMessage.error(t('common.failed')) }
  try { users.value = await api.get('/user') } catch {}
}
onMounted(load)

const saveRole = async () => {
  saving.value = true
  try {
    const r = role.value
    // 盲审 A/B-P1a 修：敏感级编码 resource='sensitivity:<v>'+effect='allow'
    // （原样上送撞后端白名单 allow|deny 必 400,且三连写已部分提交）
    const res1 = await api.post(`/permissions/${r}`, { permissions: apiSel[r] })
    await api.post(`/permissions/${r}?dimension=nav`,
      { resources: Object.fromEntries(Object.entries(navSel[r] || {}).filter(([, v]) => v)) })
    const dataRes = Object.fromEntries((dataMarketSel[r] || []).map(m => [m, 'allow']))
    if (dataSensSel[r]) dataRes[`sensitivity:${dataSensSel[r]}`] = 'allow'
    await api.post(`/permissions/${r}?dimension=data`, { resources: dataRes })
    if (res1?.preserved_locked?.length)
      ElMessage.info(t('perm.preservedInfo') + ': ' + res1.preserved_locked.join(', '))
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)) }
  finally { saving.value = false }
}

const newOv = reactive({ dimension: 'api', resource: '', effect: 'allow' })
const addOverride = async () => {
  if (!newOv.resource) return ElMessage.warning(t('perm.resource'))
  try {
    await api.post(`/permissions/user/${userSel.value}`, { ...newOv })
    ElMessage.success(t('common.success')); newOv.resource = ''; await load()
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)) }
}
const clearOverride = async row => {
  try {
    await api.post(`/permissions/user/${row.username}`,
      { dimension: row.dimension, resource: row.resource, effect: 'clear' })
    await load()
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)) }
}
</script>
