<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('factors.title') }}</span>
        <el-button type="primary" @click="openCreate">{{ t('factors.create') }}</el-button>
      </div>
    </template>
    <el-table :data="factors" stripe>
      <el-table-column prop="name" :label="t('common.name')" width="150" />
      <el-table-column :label="t('factors.category')" width="100">
        <template #default="{ row }"><el-tag>{{ row.category }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.type')" width="80">
        <template #default="{ row }">
          <el-tag v-if="row.is_custom" type="warning">{{ t('factors.custom') }}</el-tag>
          <el-tag v-else type="info">{{ t('factors.preset') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('factors.staticFactor') + '/' + t('factors.dynamicFactor')" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.needs_history === 0" type="success">{{ t('factors.staticFactor') }}</el-tag>
          <el-tag v-else type="danger">{{ t('factors.dynamicFactor') }}({{ row.needs_history }})</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('factors.usedBy')" width="90">
        <template #default="{ row }">
          <el-link v-if="usedByCount(row.name)" type="primary" @click="showRefs(row.name)">{{ usedByCount(row.name) }} ↗</el-link>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column prop="description" :label="t('common.description')" />
      <el-table-column :label="t('factors.paramsCol')" width="200">
        <template #default="{ row }">
          <el-tag v-for="(v, k) in (row.params || {})" :key="k" size="small" style="margin: 2px">{{ k }}={{ v }}</el-tag>
          <span v-if="!row.params || !Object.keys(row.params).length">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="200" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="previewFactorFor(row)">{{ t('factors.preview') }}</el-button>
          <el-button v-if="row.is_custom" size="small" type="primary" @click="openEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button v-if="row.is_custom" size="small" type="danger" @click="onDelete(row.name)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 被引用策略列表弹窗(P2-7) -->
    <el-dialog v-model="refsDlg" :title="t('factors.refsTitle', { name: refsFactor })" width="480px">
      <el-table :data="refsList" stripe size="small">
        <el-table-column prop="name" :label="t('common.name')" />
        <el-table-column :label="t('factors.weightInStrategy')" width="80">
          <template #default="{ row }">
            {{ (row.factors || []).find(f => f.name === refsFactor)?.weight ?? '—' }}
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!refsList.length" style="color: var(--text-secondary); text-align: center; padding: 20px">—</div>
    </el-dialog>

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
        <el-form-item :label="t('factors.pvParams')">
          <el-input v-model="preview.symbol" style="width: 150px" placeholder="600000.SHSE" />
          <el-select v-model="preview.freq" style="width: 80px; margin-left: 6px">
            <el-option v-for="f in ['1D','1min','5min']" :key="f" :value="f" :label="f" />
          </el-select>
          <el-input-number v-model="preview.bars" :min="20" :max="500" :step="20" style="margin-left: 6px" />
        </el-form-item>
        <el-form-item :label="t('factors.pythonCode')">
          <div style="width: 100%">
            <div style="margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-secondary)">
              {{ t('factors.codeHint') }}
            </div>
            <PythonEditor v-model="form.code" :height="300" />
            <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center">
              <el-button type="primary" @click="validateCode" :loading="validating">{{ t('factors.validate') }}</el-button>
              <el-button type="success" @click="previewFactor" :loading="previewing">{{ t('factors.preview') }}</el-button>
              <span v-if="codeValid === true" style="color: var(--el-color-success)">✅ {{ t('factors.codeValid') }}</span>
              <span v-else-if="codeValid === false" style="color: var(--el-color-danger)">❌ {{ codeError }}</span>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving">{{ t('factors.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 链条打磨#5：试算结果抽屉 -->
    <el-drawer v-model="previewVisible" :title="t('factors.previewTitle')" size="50%">
      <div v-if="previewError" style="color: var(--el-color-danger)">{{ previewError }}</div>
      <template v-else-if="previewData">
        <el-descriptions :column="5" border size="small" style="margin-bottom: 16px">
          <el-descriptions-item :label="t('factors.pvCount')">{{ previewData.stats.count }}</el-descriptions-item>
          <el-descriptions-item :label="t('factors.pvErrors')">{{ previewData.stats.errors }}</el-descriptions-item>
          <el-descriptions-item :label="t('factors.pvMin')">{{ previewData.stats.min }}</el-descriptions-item>
          <el-descriptions-item :label="t('factors.pvMax')">{{ previewData.stats.max }}</el-descriptions-item>
          <el-descriptions-item :label="t('factors.pvLast')">{{ previewData.stats.last }}</el-descriptions-item>
        </el-descriptions>
        <div ref="previewChart" style="height: 300px"></div>
      </template>
    </el-drawer>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getFactorList, createFactor, updateFactor, deleteFactor, validateFactorCode , apiErr } from '../api'
import api from '../api'
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

const previewing = ref(false)
const previewVisible = ref(false)
const previewData = ref(null)
// P2-7（05 §5.5 要点 1）：试算三参数放开——不同标的/频率的行为差异正是研究内容
const preview = ref({ symbol: '600000.SHSE', freq: '1D', bars: 60 })
const previewError = ref('')
const previewChart = ref(null)
const previewFactor = async () => {
  previewing.value = true
  try {
    const res = await api.post('/factors/preview', {
      code: form.value.code, symbol: preview.symbol, freq: preview.freq, bars: preview.bars,
      params: (() => { try { return JSON.parse(form.value.params || '{}') } catch { return {} } })(),
    })
    if (res.error) {
      previewError.value = res.error
      previewData.value = null
    } else {
      previewError.value = ''
      previewData.value = res
      previewVisible.value = true
      await nextTick()
      const echarts = (await import('echarts')).default || (await import('echarts'))
      const chart = echarts.init(previewChart.value)
      const vals = res.values.filter(v => v.value !== null)
      chart.setOption({
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: vals.map(v => v.ts.slice(5, 16)) },
        yAxis: { type: 'value', scale: true },
        series: [{ type: 'line', data: vals.map(v => v.value), showSymbol: false }],
        grid: { left: 50, right: 20, top: 20, bottom: 30 },
      })
    }
  } catch (e) {
    previewError.value = e?.detail || String(e)
    previewData.value = null
    previewVisible.value = true
  } finally { previewing.value = false }
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
    try { params = JSON.parse(form.value.paramsStr || '{}') }
    catch { ElMessage.error(t('factors.paramsInvalid')); saving.value = false; return }
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
  } catch (e) { ElMessage.error(t('common.saveFailed') + ': ' + apiErr(e)) }
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

const strategyUsages = ref([])
const usedByCount = (fname) => strategyUsages.value.filter(names => names.includes(fname)).length
const loadUsages = async () => {
  try {
    const sts = await api.get('/strategy')
    strategyUsages.value = (sts || []).map(s => {
      try { return ((typeof s.factors === 'string' ? JSON.parse(s.factors) : s.factors) || []).map(f => f.name) }
      catch { return [] }
    })
  } catch { strategyUsages.value = [] }
}
onMounted(() => { load(); loadUsages() })
</script>
// P2-7(05 §5.5):被引用可点(弹策略列表+各策略权重)+操作列预设因子试算
const refsDlg = ref(false)
const refsList = ref([])
const refsFactor = ref('')
const showRefs = (fname) => {
  refsFactor.value = fname
  refsList.value = (strategies.value || []).filter(st =>
    (st.factors || []).some(f => f.name === fname))
  refsDlg.value = true
}
const previewFactorFor = async (row) => {
  try {
    const res = await api.post('/factors/preview', {
      code: row.code || '', symbol: preview.value.symbol, freq: preview.value.freq, bars: preview.value.bars,
      params: row.params || {},
    })
    if (res.error) { ElMessage.error(res.error); return }
    previewData.value = res; previewVisible.value = true
  } catch (e) { ElMessage.error(String(e)) }
}
import { ElMessage as _EM } from 'element-plus'
