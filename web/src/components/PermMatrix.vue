<template>
  <!-- 批11B：角色权限三维矩阵组件（从 Permissions.vue 角色模式抽取——组弹窗与对照页同源零分叉）。
       自加载 GET /permissions（keys/locked/nav/data 全景），props.group 定位当前组；save 三连发沿旧链
       （POST /permissions/{group} ×3 维——非原子=存量债,部分失败时已保存维度不再重放,注释在案）。 -->
  <div>
    <el-tabs v-model="tab">
      <el-tab-pane name="api">
        <template #label><b>{{ t('perm.tabApi') }}</b></template>
        <el-checkbox-group v-model="apiSel">
          <el-checkbox v-for="k in keys" :key="k" :value="k"
                       :disabled="lockedKeys.includes(k)" style="margin: 6px 14px">
            {{ k }}<span v-if="lockedKeys.includes(k)"> 🔒</span>
          </el-checkbox>
        </el-checkbox-group>
        <div v-if="lockedKeys.length" style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">
          {{ t('perm.lockedNote') }}: {{ lockedKeys.join(' / ') }}
        </div>
      </el-tab-pane>
      <el-tab-pane name="nav">
        <template #label><b>{{ t('perm.tabNav') }}</b></template>
        <el-table :data="navItems" size="small" max-height="320">
          <el-table-column prop="id" :label="t('common.name')" width="140" />
          <el-table-column prop="group" :label="t('perm.navGroup')" width="110" />
          <el-table-column :label="t('perm.navState')">
            <template #default="{ row }">
              <el-radio-group :model-value="navSel[row.id] || ''" size="small"
                              @update:model-value="v => navSel[row.id] = v">
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
        <el-checkbox-group v-model="dataMarketSel">
          <el-checkbox v-for="m in dataFields.markets" :key="m" :value="m" style="margin: 6px 14px">{{ m }}</el-checkbox>
        </el-checkbox-group>
        <div style="margin: 12px 0 6px; font-weight: 600">{{ t('perm.dataSens') }}</div>
        <el-radio-group :model-value="dataSensSel" @update:model-value="v => dataSensSel = v">
          <el-radio-button value="detail">{{ t('perm.sensDetail') }}</el-radio-button>
          <el-radio-button value="aggregated">{{ t('perm.sensAgg') }}</el-radio-button>
          <el-radio-button value="count">{{ t('perm.sensCount') }}</el-radio-button>
        </el-radio-group>
        <div style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">{{ t('perm.dataNote') }}</div>
      </el-tab-pane>
    </el-tabs>
    <el-button type="primary" @click="save" :loading="saving" style="margin-top: 14px">{{ t('common.save') }}</el-button>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'

const props = defineProps({ group: { type: String, required: true } })
const emit = defineEmits(['saved'])
const { t } = useI18n()

const tab = ref('api')
const saving = ref(false)
const keys = ref([])
const lockedKeys = ref([])          // 后端随 GET 返回（批11B：前端不再硬编码 🔒）
const navItems = ref([])
const dataFields = reactive({ markets: [], sensitivity: [] })
const apiSel = ref([])
const navSel = reactive({})
const dataMarketSel = ref([])
const dataSensSel = ref('')

const load = async () => {
  try {
    const r = await api.get('/permissions')
    keys.value = r.keys || []
    lockedKeys.value = r.locked || []
    navItems.value = (r.nav?.items) || []
    dataFields.markets = r.data?.fields?.markets || []
    dataFields.sensitivity = r.data?.fields?.sensitivity || []
    const g = props.group
    apiSel.value = [...(r.roles?.[g] || [])]
    if (!apiSel.value.length) apiSel.value = ['read']   // 新组零配置默认勾 read（破 EMPTY_PERMISSIONS，盲审 B P1-4）
    Object.assign(navSel, r.nav?.roles?.[g] || {})   // 盲审 B P0-1：reactive 对象禁 .value= 赋值（旧错型致 nav 三态加载恒空+保存必 400）
    const m = r.data?.roles?.[g] || {}
    dataMarketSel.value = Object.entries(m)
      .filter(([k, e]) => e === 'allow' && !k.startsWith('sensitivity:')).map(([k]) => k)
    const sensKey = Object.keys(m).find(k => k.startsWith('sensitivity:'))
    dataSensSel.value = sensKey ? sensKey.slice('sensitivity:'.length) : ''
  } catch { ElMessage.error(t('common.loadFailed')) }
}
watch(() => props.group, load, { immediate: true })

const save = async () => {
  saving.value = true
  try {
    const g = props.group
    const res1 = await api.post(`/permissions/${g}`, { permissions: apiSel.value })
    await api.post(`/permissions/${g}?dimension=nav`,
      { resources: Object.fromEntries(Object.entries(navSel).filter(([, v]) => v)) })
    const dataRes = Object.fromEntries(dataMarketSel.value.map(m => [m, 'allow']))
    if (dataSensSel.value) dataRes[`sensitivity:${dataSensSel.value}`] = 'allow'
    await api.post(`/permissions/${g}?dimension=data`, { resources: dataRes })
    if (res1?.preserved_locked?.length)
      ElMessage.info(t('perm.preservedInfo') + ': ' + res1.preserved_locked.join(', '))
    ElMessage.success(t('common.saveSuccess'))
    emit('saved')
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)) }
  finally { saving.value = false }
}
</script>
