<template>
  <!-- W4（web-design 10 §4 三维矩阵）：角色基线 × 用户 override 双模式。
      批11B：角色模式三维编辑抽为 PermMatrix 组件（与用户管理·群组弹窗同源零分叉）；
      角色下拉动态（user_group 表）。本页保留作对照（用户裁定④，删除另行通知）。 -->
  <el-card>
    <template #header>{{ t('perm.title') }}</template>
    <el-alert type="info" :closable="false" style="margin-bottom: 14px">{{ t('perm.note') }}</el-alert>

    <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 14px">
      <el-radio-group v-model="mode">
        <el-radio-button value="role">{{ t('perm.modeRole') }}</el-radio-button>
        <el-radio-button value="user">{{ t('perm.modeUser') }}</el-radio-button>
      </el-radio-group>
      <el-select v-if="mode === 'role'" v-model="role" style="width: 160px">
        <el-option v-for="g in groups" :key="g.name" :value="g.name" :label="g.name" />
      </el-select>
      <el-select v-else v-model="userSel" :placeholder="t('perm.pickUser')" style="width: 200px" filterable>
        <el-option v-for="u in users" :key="u.username" :value="u.username" :label="`${u.username} (${u.role})`" />
      </el-select>
    </div>

    <!-- ═══ 角色基线模式：PermMatrix（批11B 同源组件） ═══ -->
    <PermMatrix v-if="mode === 'role' && role" :key="role" :group="role" />

    <!-- ═══ 用户 override 模式（结构不变） ═══ -->
    <template v-else-if="mode === 'user'">
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
        <div style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">
          {{ t('perm.lockedNote') }}: user_mgmt / resume / account_keys
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
import PermMatrix from '../components/PermMatrix.vue'
const { t } = useI18n()

const mode = ref('role')
const role = ref('viewer')
const userSel = ref('')
const groups = ref([])   // 批11B：动态（user_group 表）
const users = ref([])
const overrides = ref([])

const userOverrides = u => overrides.value.filter(o => o.username === u)

const load = async () => {
  try { groups.value = await api.get('/user-groups') } catch {}
  if (groups.value.length && !groups.value.some(g => g.name === role.value))
    role.value = groups.value[0].name
  try {
    const r = await api.get('/permissions')
    overrides.value = r.user_overrides || []
  } catch { ElMessage.error(t('common.failed')) }
  try { users.value = await api.get('/user') } catch {}
}
onMounted(load)

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
