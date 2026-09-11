<template>
  <!-- 批11B：角色权限三维矩阵组件（从 Permissions.vue 角色模式抽取——组弹窗与对照页同源零分叉）。
       批15：data 维退役（脱敏删+markets 存而不灵），换 market_op（市场操作权限——勾=allow/不勾=deny，
       全量重写语义，check_order 2.5 卡口消费）。自加载 GET /permissions（keys/locked/nav/market_op 全景），
       props.group 定位当前组；save 三连发沿旧链（POST /permissions/{group} ×3 维——非原子=存量债,
       部分失败时已保存维度不再重放,注释在案）。 -->
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
      <el-tab-pane name="market">
        <template #label><b>{{ t('perm.tabMarket') }}</b></template>
        <el-checkbox-group v-model="marketSel">
          <el-checkbox v-for="m in marketKeys" :key="m" :value="m" style="margin: 6px 14px">
            {{ t(`perm.mk_${m}`) }}
          </el-checkbox>
        </el-checkbox-group>
        <div style="color: var(--text-secondary); font-size: 12px; margin-top: var(--sp-2)">
          {{ t('perm.marketOpNote') }}
        </div>
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
const marketKeys = ref([])          // 批15：market_op 五键（后端 _MARKET_OP_KEYS 单源下发）
const apiSel = ref([])
const navSel = reactive({})
const marketSel = ref([])

const load = async () => {
  try {
    const r = await api.get('/permissions')
    keys.value = r.keys || []
    lockedKeys.value = r.locked || []
    navItems.value = (r.nav?.items) || []
    marketKeys.value = r.market_op?.keys || []
    const g = props.group
    apiSel.value = [...(r.roles?.[g] || [])]
    if (!apiSel.value.length) apiSel.value = ['read']   // 新组零配置默认勾 read（破 EMPTY_PERMISSIONS，盲审 B P1-4）
    Object.assign(navSel, r.nav?.roles?.[g] || {})   // 盲审 B P0-1：reactive 对象禁 .value= 赋值（旧错型致 nav 三态加载恒空+保存必 400）
    const m = r.market_op?.roles?.[g] || {}
    marketSel.value = marketKeys.value.filter(k => m[k] === 'allow')
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
    // market_op 全量重写：勾=allow、不勾=deny（无行≠未配置语义——矩阵所见即卡口所得）
    const marketRes = Object.fromEntries(
      marketKeys.value.map(k => [k, marketSel.value.includes(k) ? 'allow' : 'deny']))
    await api.post(`/permissions/${g}?dimension=market_op`, { resources: marketRes })
    if (res1?.preserved_locked?.length)
      ElMessage.info(t('perm.preservedInfo') + ': ' + res1.preserved_locked.join(', '))
    ElMessage.success(t('common.saveSuccess'))
    emit('saved')
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)) }
  finally { saving.value = false }
}
</script>
