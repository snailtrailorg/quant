<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('factors.title') }}</span>
        <el-button type="primary" size="small" @click="openCreate">{{ t('factors.create') }}</el-button>
      </div>
    </template>
    <el-table :data="factors" stripe>
      <el-table-column prop="name" :label="t('common.name')" width="150" />
      <el-table-column :label="t('factors.category')" width="100">
        <template #default="{ row }"><el-tag size="small">{{ row.category }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.type')" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.is_custom" type="warning" size="small">{{ t('factors.custom') }}</el-tag>
          <el-tag v-else type="info" size="small">{{ t('factors.preset') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('factors.staticFactor') + '/' + t('factors.dynamicFactor')" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.needs_history === 0" type="success" size="small">{{ t('factors.staticFactor') }}</el-tag>
          <el-tag v-else type="danger" size="small">{{ t('factors.dynamicFactor') }}({{ row.needs_history }})</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" :label="t('common.description')" />
      <el-table-column :label="t('factors.paramsCol')" width="200">
        <template #default="{ row }">{{ JSON.stringify(row.params) }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="150" v-if="hasCustom">
        <template #default="{ row }">
          <el-button v-if="row.is_custom" size="small" @click="openEdit(row)">{{ t('factors.edit') }}</el-button>
          <el-button v-if="row.is_custom" size="small" type="danger" @click="onDelete(row.name)">{{ t('factors.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="isEditing ? t('factors.editFactor') : t('factors.createFactor')" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="100px" v-loading="saving">
        <el-form-item :label="t('factors.factorName')">
          <el-input v-model="form.name" :disabled="isEditing" />
        </el-form-item>
        <el-form-item :label="t('factors.adaptCategory')">
          <el-select v-model="form.category" style="width: 100%">
            <el-option :label="t('factors.catTrend')" value="trend" />
            <el-option :label="t('factors.catMeanrev')" value="meanrev" />
            <el-option :label="t('factors.catConvertible')" value="convertible" />
            <el-option :label="t('factors.catCrypto')" value="crypto" />
            <el-option :label="t('factors.catCustom')" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.description')">
          <el-input v-model="form.description" />
        </el-form-item>
        <el-form-item :label="t('factors.defaultParams')">
          <el-input v-model="form.paramsStr" :placeholder="t('factors.phParams')" />
        </el-form-item>
        <el-form-item :label="t('factors.historyWindow')">
          <el-input-number v-model="form.needsHistory" :min="0" :step="1" />
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            {{ t('factors.historyHint') }}
          </div>
        </el-form-item>
        <el-form-item :label="t('factors.pythonCode')">
          <div style="width: 100%">
            <div style="margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-secondary)">
              {{ t('factors.codeHint') }}
            </div>
            <PythonEditor v-model="form.code" :height="300" />
            <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center">
              <el-button size="small" @click="validateCode" :loading="validating">{{ t('factors.validate') }}</el-button>
              <span v-if="codeValid === true" style="color: var(--el-color-success)">✅ {{ t('factors.codeValid') }}</span>
              <span v-else-if="codeValid === false" style="color: var(--el-color-danger)">❌ {{ codeError }}</span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving">{{ t('factors.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getFactorList, createFactor, updateFactor, deleteFactor, validateFactorCode } from '../api'
import PythonEditor from '../components/PythonEditor.vue'

const { t } = useI18n()
const factors = ref([])
const dialogVisible = ref(false)
const isEditing = ref(false)
const saving = ref(false)
const validating = ref(false)
const codeValid = ref(null)
const codeError = ref('')

const DEFAULT_CODE = `def compute(ctx, n=20):
    """计算因子值。

    ctx 可用属性/方法：
      ctx.close, ctx.high, ctx.low, ctx.open_, ctx.volume  — 当前 bar 值
      ctx.history — 历史 bar 列表（每项是 dict，有 close/high/low/open_/volume）
      ctx.sma(n)  — 简单移动平均
    """
    closes = [h.get("close", 0) for h in ctx.history[-(n-1):]] + [ctx.close]
    sma = sum(closes) / len(closes)
    return ctx.close / sma - 1
`

const form = ref({
  name: '', category: 'custom', description: '', code: DEFAULT_CODE, paramsStr: '{}', needsHistory: 0,
})

const hasCustom = computed(() => factors.value.some(f => f.is_custom))

const load = async () => {
  try {
    const r = await getFactorList()
    factors.value = r.items || []
  } catch (e) { ElMessage.error(t('factors.loadFailed')) }
}

const openCreate = () => {
  isEditing.value = false
  form.value = { name: '', category: 'custom', description: '', code: DEFAULT_CODE, paramsStr: '{}', needsHistory: 0 }
  codeValid.value = null
  codeError.value = ''
  dialogVisible.value = true
}

const openEdit = (row) => {
  isEditing.value = true
  form.value = {
    name: row.name,
    category: row.category || 'custom',
    description: row.description || '',
    code: row.code || '',
    paramsStr: JSON.stringify(row.params || {}, null, 2),
    needsHistory: row.needs_history || 0,
  }
  codeValid.value = null
  codeError.value = ''
  dialogVisible.value = true
}

const validateCode = async () => {
  validating.value = true
  codeValid.value = null
  codeError.value = ''
  try {
    const res = await validateFactorCode(form.value.code, form.value.name || 'test')
    if (res.valid) {
      codeValid.value = true
      ElMessage.success(t('factors.codeValid'))
    } else {
      codeValid.value = false
      codeError.value = res.error || t('factors.validateFailed')
      ElMessage.error(t('factors.codeInvalid') + ': ' + (res.error || ''))
    }
  } catch (e) {
    codeValid.value = false
    codeError.value = e?.message || t('factors.validateReqFailed')
  }
  finally { validating.value = false }
}

const save = async () => {
  if (!form.value.name) { ElMessage.warning(t('factors.nameRequired')); return }
  saving.value = true
  try {
    let params = {}
    try { params = JSON.parse(form.value.paramsStr || '{}') } catch { params = {} }
    const data = {
      name: form.value.name,
      category: form.value.category,
      description: form.value.description,
      code: form.value.code,
      params,
      needs_history: form.value.needsHistory,
    }
    if (isEditing.value) {
      await updateFactor(form.value.name, data)
      ElMessage.success(t('common.updateSuccess'))
    } else {
      await createFactor(data)
      ElMessage.success(t('common.createSuccess'))
    }
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.saveFailed') + ': ' + (e?.error || e?.message || '')) }
  finally { saving.value = false }
}

const onDelete = async (name) => {
  try {
    await ElMessageBox.confirm(t('factors.confirmDelete', { name }), t('common.confirm'))
    await deleteFactor(name)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch { /* 取消 */ }
}

onMounted(load)
</script>