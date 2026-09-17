<template>
  <el-card>
    <template #header><div style="display:flex; justify-content:space-between; align-items:center">{{ t('riskRule.title') }}<IconBtn :icon="Plus" :title="t('common.create')" @click="onAdd" /></div></template>
    <!-- 批17 17A：列宽拖拽+持久化 -->
    <TableShell :data="rules" storage-key="risk-rules">
      <el-table-column prop="name" :label="t('common.name')" min-width="140" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('common.type')" min-width="160">
        <template #default="{ row }">{{ typeLabel(row.type) }}</template>
      </el-table-column>
      <el-table-column prop="params" :label="t('riskRule.params')" min-width="200" show-overflow-tooltip />
      <el-table-column prop="updated_at" :label="t('common.updatedAt')" min-width="160">
        <template #default="{ row }">{{ fmtTime.full(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column prop="enabled" :label="t('common.enable')" min-width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" min-width="180">
        <template #default="{ row }">
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="onEdit(row)" />
          <IconBtn size="small" :icon="Delete" :title="t('common.delete')" type="danger" @click="onDelete(row.id)" :disabled="navReadonly" />
        </template>
      </el-table-column>
    </TableShell>
    <!-- 批36a-1（用户裁定=活参数入 UI）：params 纯文本 JSON → schema 驱动动态表单
         （后端 GET /types 单源下发 lo/hi/step/precision/enum——前端纯渲染零硬编码） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false" :title="form.id ? t('riskRule.editRule') : t('riskRule.addRule')" width="560px">
      <el-form :model="form" label-width="140px">
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.type')">
        <el-select v-model="form.type" style="width: 100%" @change="onTypeChange">
          <el-option v-for="ty in types" :key="ty.type" :value="ty.type"
                     :label="typeLabel(ty.type) + (ty.active ? '' : `（${t('riskRule.typeInactive')}）`)">
            <span :style="ty.active ? '' : 'color: var(--text-secondary)'">
              {{ typeLabel(ty.type) }}{{ ty.active ? '' : `（${t('riskRule.typeInactive')}）` }}
            </span>
          </el-option>
        </el-select>
        <div v-if="!currentSpec?.active" class="param-hint">{{ t('riskRule.typeInactiveNote') }}</div>
      </el-form-item>
      <el-form-item v-for="p in currentSpec?.params || []" :key="p.key" :label="paramLabel(p.key)">
        <el-input-number v-if="p.dtype === 'float' || p.dtype === 'int'"
          v-model="form.values[p.key]" :min="p.lo ?? undefined" :max="p.hi ?? undefined"
          :step="p.step || 1" :precision="p.precision ?? undefined" style="width: 180px" />
        <el-switch v-else-if="p.dtype === 'bool'" v-model="form.values[p.key]" />
        <el-select v-else v-model="form.values[p.key]" style="width: 180px">
          <el-option v-for="v in p.values || []" :key="v" :value="v" :label="v" />
        </el-select>
        <div v-if="p.zero_note" class="param-hint">{{ t('riskRule.paramZeroNote') }}</div>
      </el-form-item>
      <el-form-item v-if="currentSpec?.params?.length"><span class="param-hint">{{ t('riskRule.paramHint') }}</span></el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="onSave" :loading="saving" :disabled="navReadonly">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { fmtTime } from '../utils/fmtTime'
import TableShell from '../components/TableShell.vue'
import IconBtn from '../components/IconBtn.vue'
import { Plus, Edit, Delete } from '@element-plus/icons-vue'
import {apiErr,  getRiskRules, getRiskRuleTypes, createRiskRule, updateRiskRule, deleteRiskRule } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const rules = ref([])
const types = ref([])   // 批36a-1：[{type, active, params:[{key,dtype,lo,hi,...}]}]——schema 单源下发
const form = ref(emptyForm())
const saving = ref(false)
const dlg = ref(false)   // 编辑形态弹窗化（DESIGN 新立法）

const TYPE_KEYS = { global: 'typeGlobal', etf_conv: 'typeEtfConv', crypto: 'typeCrypto',
                    max_position: 'typeMaxPosition', max_single_order: 'typeMaxSingleOrder',
                    daily_loss_limit: 'typeDailyLossLimit' }
const typeLabel = ty => t(`riskRule.${TYPE_KEYS[ty] || ty}`)
const _camel = k => k.replace(/_([a-z])/g, (_, c) => c.toUpperCase())   // schema snake_case → 词条 camelCase（盲审 A-P1-1）
const paramLabel = key => t(`riskRule.param.${_camel(key)}`)

const currentSpec = computed(() => types.value.find(ty => ty.type === form.value.type))

function defaultsOf(spec) {
  const v = {}
  for (const p of spec?.params || []) v[p.key] = p.default
  return v
}
function emptyForm() {
  return { name: '', type: 'global', values: {}, enabled: true }
}

const load = async () => {
  try {
    rules.value = await getRiskRules()
    types.value = (await getRiskRuleTypes()).types || []
    if (!form.value.values || !Object.keys(form.value.values).length) onTypeChange()
  } catch (e) { console.error(e) }
}
onMounted(load)

const onTypeChange = () => { form.value.values = defaultsOf(currentSpec.value) }
const onAdd = () => { form.value = emptyForm(); onTypeChange(); dlg.value = true }
const onEdit = (row) => {
  form.value = { id: row.id, name: row.name, type: row.type, values: {}, enabled: row.enabled }
  let loaded = {}
  try { loaded = JSON.parse(row.params || '{}') } catch { loaded = {} }
  const spec = types.value.find(ty => ty.type === row.type)
  form.value.values = { ...defaultsOf(spec), ...loaded }   // 未知/缺省键回落 default
  dlg.value = true
}

const onSave = async () => {
  // 批36a-1：前端友好校验（开区间下界 EP :min 闭区间表达不了——保存前显式拦；后端 400 兜底）
  for (const p of currentSpec.value?.params || []) {
    const v = form.value.values[p.key]
    if ((p.dtype === 'float' || p.dtype === 'int') && typeof v === 'number') {
      if (p.lo_open && v <= p.lo) { ElMessage.warning(t('riskRule.loOpenErr', { k: paramLabel(p.key), lo: p.lo })); return }
      if (typeof p.hi === 'number' && v > p.hi) { ElMessage.warning(t('riskRule.hiErr', { k: paramLabel(p.key), hi: p.hi })); return }
    }
  }
  saving.value = true
  try {
    const body = { name: form.value.name, type: form.value.type, params: JSON.stringify(form.value.values), enabled: form.value.enabled }
    if (form.value.id) await updateRiskRule(form.value.id, body)
    else await createRiskRule(body)
    ElMessage.success(t('common.saveSuccess'))
    form.value = emptyForm()
    dlg.value = false
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteRiskRule(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}
</script>
<style scoped>
.param-hint { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.4; margin-top: 2px; }
</style>
