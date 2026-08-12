<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('factors.title') }}</span>
        <el-button type="primary" size="small" @click="openCreate">{{ t('factors.create') }}</el-button>
      </div>
    </template>
    <el-table :data="factors" stripe>
      <el-table-column prop="name" label="名称" width="150" />
      <el-table-column label="类别" width="100">
        <template #default="{ row }"><el-tag size="small">{{ row.category }}</el-tag></template>
      </el-table-column>
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.is_custom" type="warning" size="small">自定义</el-tag>
          <el-tag v-else type="info" size="small">预置</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="静态/动态" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.needs_history === 0" type="success" size="small">静态</el-tag>
          <el-tag v-else type="danger" size="small">动态({{ row.needs_history }})</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="描述" />
      <el-table-column label="参数" width="200">
        <template #default="{ row }">{{ JSON.stringify(row.params) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" v-if="hasCustom">
        <template #default="{ row }">
          <el-button v-if="row.is_custom" size="small" @click="openEdit(row)">{{ t('factors.edit') }}</el-button>
          <el-button v-if="row.is_custom" size="small" type="danger" @click="onDelete(row.name)">{{ t('factors.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑因子' : '新建因子'" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="100px" v-loading="saving">
        <el-form-item label="因子名称">
          <el-input v-model="form.name" :disabled="isEditing" />
        </el-form-item>
        <el-form-item label="适配品类">
          <el-select v-model="form.category" style="width: 100%">
            <el-option label="趋势" value="trend" />
            <el-option label="均值回归" value="meanrev" />
            <el-option label="可转债" value="convertible" />
            <el-option label="加密" value="crypto" />
            <el-option label="通用" value="custom" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" />
        </el-form-item>
        <el-form-item label="默认参数">
          <el-input v-model="form.paramsStr" placeholder='如 {"n": 20}' />
        </el-form-item>
        <el-form-item label="历史窗口">
          <el-input-number v-model="form.needsHistory" :min="0" :step="1" />
          <div style="color: #999; font-size: 12px; margin-top: 4px">
            0=静态因子（只用当前 bar，可选股+策略）；>0=动态因子（需历史窗口N，只能用于策略）
          </div>
        </el-form-item>
        <el-form-item label="Python 代码">
          <div style="width: 100%">
            <div style="margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-secondary)">
              定义 compute(ctx, **params) 函数，ctx 有 close/high/low/open_/volume/history/sma() 方法
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
        <el-button @click="dialogVisible = false">取消</el-button>
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
  } catch (e) { ElMessage.error('加载因子失败') }
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
      codeError.value = res.error || '校验失败'
      ElMessage.error(t('factors.codeValid') + ': ' + (res.error || ''))
    }
  } catch (e) {
    codeValid.value = false
    codeError.value = e?.message || '校验请求失败'
  }
  finally { validating.value = false }
}

const save = async () => {
  if (!form.value.name) { ElMessage.warning('名称必填'); return }
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
      ElMessage.success('已更新')
    } else {
      await createFactor(data)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error('保存失败: ' + (e?.error || e?.message || '')) }
  finally { saving.value = false }
}

const onDelete = async (name) => {
  try {
    await ElMessageBox.confirm(`确认删除因子 "${name}"？`, '确认')
    await deleteFactor(name)
    ElMessage.success('已删除')
    await load()
  } catch { /* 取消 */ }
}

onMounted(load)
</script>