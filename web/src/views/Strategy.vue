<template>
  <el-card>
    <template #header>{{ t('strategy.title') }}</template>
    <el-table :data="strategies" stripe>
      <el-table-column prop="name" :label="t('strategy.name')" />
      <el-table-column prop="type" :label="t('strategy.type')" />
      <el-table-column prop="symbol" :label="t('strategy.symbol')" />
      <el-table-column :label="t('strategy.status')">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '运行中' : '已停' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="success" @click="onStart(row.id)" v-if="!row.enabled">{{ t('strategy.start') }}</el-button>
          <el-button size="small" type="danger" @click="onStop(row.id)" v-if="row.enabled">{{ t('strategy.stop') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑策略" width="640px" :close-on-click-modal="false">
      <el-form :model="editForm" label-width="100px" v-loading="saving">
        <el-form-item label="名称">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="标的">
          <el-input v-model="editForm.symbol" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" />
        </el-form-item>

        <el-divider content-position="left">因子配置</el-divider>
        <div v-for="(f, i) in editForm.factors" :key="i" style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center">
          <el-select v-model="f.name" placeholder="选择因子" style="width: 180px" @change="onFactorChange(f)">
            <el-option v-for="fac in availableFactors" :key="fac.name" :label="`${fac.name} (${fac.category})`" :value="fac.name" />
          </el-select>
          <el-input-number v-model="f.weight" :min="0" :max="2" :step="0.1" :precision="2" style="width: 120px" />
          <el-button size="small" type="danger" @click="editForm.factors.splice(i, 1)">删</el-button>
        </div>
        <el-button size="small" @click="addFactor">+ 添加因子</el-button>

        <el-divider content-position="left">信号聚合</el-divider>
        <el-form-item label="买入阈值">
          <el-input-number v-model="editForm.aggregator.threshold_buy" :step="0.1" :precision="2" />
        </el-form-item>
        <el-form-item label="卖出阈值">
          <el-input-number v-model="editForm.aggregator.threshold_sell" :step="0.1" :precision="2" />
        </el-form-item>

        <el-divider content-position="left">DSL 表达式（可选）</el-divider>
        <el-form-item label="表达式">
          <el-input v-model="editForm.dslExpr" type="textarea" :rows="3" placeholder="如：ma_dev * 0.6 + rsi * 0.4" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getStrategies, startStrategy, stopStrategy, updateStrategy, getFactorList } from '../api'

const { t } = useI18n()
const strategies = ref([])
const availableFactors = ref([])
const editVisible = ref(false)
const saving = ref(false)
const editForm = ref({ id: '', name: '', symbol: '', enabled: true, factors: [], aggregator: { threshold_buy: 0.3, threshold_sell: -0.3 }, dslExpr: '' })

const load = async () => { strategies.value = await getStrategies() }
const loadFactors = async () => { const r = await getFactorList(); availableFactors.value = r.items || [] }

const openEdit = (row) => {
  editForm.value = {
    id: row.id,
    name: row.name,
    symbol: row.symbol,
    enabled: row.enabled,
    factors: (row.factors || []).map(f => ({ name: f.name, weight: f.weight, params: f.params || {} })),
    aggregator: { ...row.aggregator } || { threshold_buy: 0.3, threshold_sell: -0.3 },
    dslExpr: row.params?.dsl_expr || '',
  }
  editVisible.value = true
}

const addFactor = () => { editForm.value.factors.push({ name: '', weight: 0.5, params: {} }) }
const onFactorChange = (f) => {
  const fac = availableFactors.value.find(x => x.name === f.name)
  if (fac) f.params = { ...fac.params }
}

const saveEdit = async () => {
  saving.value = true
  try {
    const params = editForm.value.dslExpr ? { dsl_expr: editForm.value.dslExpr } : {}
    await updateStrategy(editForm.value.id, {
      name: editForm.value.name,
      symbol: editForm.value.symbol,
      enabled: editForm.value.enabled,
      factors: editForm.value.factors.filter(f => f.name),
      aggregator: editForm.value.aggregator,
      params,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch (e) { ElMessage.error('保存失败') }
  finally { saving.value = false }
}

const onStart = async id => { await startStrategy(id); ElMessage.success('已启动'); load() }
const onStop = async id => { await stopStrategy(id); ElMessage.success('已停止'); load() }
onMounted(async () => { await load(); await loadFactors() })
</script>