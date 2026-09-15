<template>
  <!-- 批11B：角色权限三维矩阵组件（从 Permissions.vue 角色模式抽取——组弹窗与对照页同源零分叉）。
       批15：data 维退役（脱敏删+markets 存而不灵），换 market_op（市场操作权限——勾=allow/不勾=deny，
       全量重写语义，check_order 2.5 卡口消费）。自加载 GET /permissions（keys/locked/nav/market_op 全景），
       props.group 定位当前组；save 三连发沿旧链（POST /permissions/{group} ×3 维——非原子=存量债,
       部分失败时已保存维度不再重放,注释在案）。 -->
  <div>
    <!-- 批27-6：未加载态遮罩——加载失败时矩阵不可当作真值编辑（空集保存=清空扩权） -->
    <el-alert v-if="!loaded" type="warning" :closable="false" show-icon
              :title="t('perm.notLoaded')" style="margin-bottom: var(--sp-2)" />
    <el-tabs v-model="tab">
      <el-tab-pane name="api">
        <template #label><b>{{ t('perm.tabApi') }}</b></template>
        <!-- 批24 迭代七（用户裁定）：分组展示+中文名（无下划线，键名留 title 悬浮给管理员）；未知键落 other 组 -->
        <div v-for="g in permGroupsOf(keys)" :key="g.id" style="margin-bottom: var(--sp-3)">
          <div class="perm-group-title">
            <span>{{ te('perm.grp_' + g.id) ? t('perm.grp_' + g.id) : g.id }}</span>
            <span v-if="te('perm.grp_' + g.id + '_desc')" class="perm-group-desc">{{ t('perm.grp_' + g.id + '_desc') }}</span>
          </div>
          <el-checkbox-group v-model="apiSel" class="perm-grid">
            <el-checkbox v-for="k in g.keys" :key="k" :value="k" :disabled="lockedKeys.includes(k)" :title="k">
              {{ te('perm.key_' + k) ? t('perm.key_' + k) : k }}<span v-if="lockedKeys.includes(k)"> 🔒</span>
            </el-checkbox>
          </el-checkbox-group>
        </div>
        <div v-if="lockedKeys.length" style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-2)">
          {{ t('perm.lockedNote') }}: {{ lockedKeys.map(k => (te('perm.key_' + k) ? t('perm.key_' + k) : k)).join(' / ') }}
        </div>
      </el-tab-pane>
      <el-tab-pane name="nav">
        <template #label><b>{{ t('perm.tabNav') }}</b></template>
        <TableShell :data="navItems" size="small" max-height="320" storage-key="perm-matrix">
          <el-table-column prop="id" :label="t('common.name')" width="140" />
          <el-table-column prop="group" :label="t('perm.navGroup')" width="110" />
          <el-table-column prop="nav_state" :label="t('perm.navState')">
            <!-- 批24 迭代五（用户裁定）：四态收三态——"—"（无配置）后端语义=readwrite 缺省（perms.py load_nav_map），
                 并入读写档（非隐藏！）；单选三态互斥；保存时 readwrite 不写行=维持"无配置即缺省"数据语义 -->
            <template #default="{ row }">
              <el-radio-group :model-value="navSel[row.id] || 'readwrite'" size="small"
                              @update:model-value="v => navSel[row.id] = v">
                <el-radio-button value="hidden">{{ t('perm.navHidden') }}</el-radio-button>
                <el-radio-button value="readonly">{{ t('perm.navReadonly') }}</el-radio-button>
                <el-radio-button value="readwrite">{{ t('perm.navReadwrite') }}</el-radio-button>
              </el-radio-group>
            </template>
          </el-table-column>
        </TableShell>
      </el-tab-pane>
      <el-tab-pane name="market">
        <template #label><b>{{ t('perm.tabMarket') }}</b></template>
        <el-checkbox-group v-model="marketSel" class="perm-grid">
          <el-checkbox v-for="m in marketKeys" :key="m" :value="m">
            {{ t(`perm.mk_${m}`) }}
          </el-checkbox>
        </el-checkbox-group>
        <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-2)">
          {{ t('perm.marketOpNote') }}
        </div>
      </el-tab-pane>
    </el-tabs>
    <!-- hideSave（批24 迭代十二）：组弹窗统一保存时隐藏组件内按钮，save 经 expose 由外层调 -->
    <el-button v-if="!hideSave" type="primary" @click="save" :loading="saving" style="margin-top: 14px">{{ t('common.save') }}</el-button>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import TableShell from './TableShell.vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
import { permGroupsOf } from '../permGroups'

const props = defineProps({
  group: { type: String, required: true },
  hideSave: { type: Boolean, default: false },   // 批24 迭代十二：外层统一保存（对照页仍用组件内钮）
})
const emit = defineEmits(['saved'])
const { t, te } = useI18n()

const tab = ref('api')
const saving = ref(false)
const keys = ref([])
const lockedKeys = ref([])          // 后端随 GET 返回（批11B：前端不再硬编码 🔒）
const navItems = ref([])
const marketKeys = ref([])          // 批15：market_op 五键（后端 _MARKET_OP_KEYS 单源下发）
const apiSel = ref([])
const navSel = reactive({})
const marketSel = ref([])
const loaded = ref(false)   // 批27-6：加载成功才可保存（失败态禁存防空集清空扩权）
let _snapshot = ''   // 批24 迭代十五：加载态快照——isDirty 判没改不发请求
const _ser = () => JSON.stringify({
  a: [...apiSel.value].sort(),
  n: Object.fromEntries(Object.entries(navSel).sort()),
  m: [...marketSel.value].sort(),
})

const load = async () => {
  loaded.value = false   // 批27-6：切组即重置——失败态保留上一组选择域=跨组污染（保存把上一组值写进当前组）
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
    _snapshot = _ser()
    loaded.value = true
  } catch { ElMessage.error(t('common.loadFailed')) }
}
watch(() => props.group, load, { immediate: true })

const save = async () => {
  // 批27-6（全局检视 B-P1-4）：未加载态禁存——加载失败时空矩阵上手动勾选再保存，
  // nav/market_op 维会以空集发出=清空该组全部行（nav 无行=缺省 readwrite=扩权）
  if (!loaded.value) {
    ElMessage.warning(t('perm.notLoaded'))
    return false
  }
  saving.value = true
  try {
    const g = props.group
    const res1 = await api.post(`/permissions/${g}`, { permissions: apiSel.value })
    await api.post(`/permissions/${g}?dimension=nav`,
      // 批24 迭代五：readwrite=不写行（无配置即缺省 readwrite——navSel 内存值保留但过滤落库）
      { resources: Object.fromEntries(Object.entries(navSel).filter(([, v]) => v && v !== 'readwrite')) })
    // market_op 全量重写：勾=allow、不勾=deny（无行≠未配置语义——矩阵所见即卡口所得）
    const marketRes = Object.fromEntries(
      marketKeys.value.map(k => [k, marketSel.value.includes(k) ? 'allow' : 'deny']))
    await api.post(`/permissions/${g}?dimension=market_op`, { resources: marketRes })
    if (res1?.preserved_locked?.length)
      ElMessage.info(t('perm.preservedInfo') + ': ' + res1.preserved_locked.map(k => (te('perm.key_' + k) ? t('perm.key_' + k) : k)).join(', '))
    ElMessage.success(t('common.saveSuccess'))
    emit('saved')
    return true
  } catch (e) { ElMessage.error(String(e?.response?.data?.detail || e)); return false }
  finally { saving.value = false }
}
defineExpose({ save, isDirty: () => loaded.value && _ser() !== _snapshot })   // 迭代十二 save 入口+迭代十五 diff 判（未改不发请求）；批27-6 未加载态恒非脏——置于 const save 定义后（TDZ）
</script>

<style scoped>
/* 批24 用户裁定：权限复选框表格化——无边框多列 grid，行列对齐（监控卡同款三列节奏）；
   迭代七：分组标题+描述（组名 600，描述灰小字） */
.perm-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-2) var(--sp-4); }
.perm-grid :deep(.el-checkbox) { margin-right: 0; height: auto; }
.perm-group-title { display: flex; align-items: baseline; gap: var(--sp-2); margin-bottom: var(--sp-1); font-size: var(--fs-label); font-weight: 600; }
.perm-group-desc { font-weight: 400; color: var(--text-secondary); font-size: var(--fs-foot); }
</style>
