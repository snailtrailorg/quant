<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('pool.manageTitle') }}</span>
        <div>
          <!-- 批16：后端有端点全站无按钮的两操作（用户裁定补） -->
          <!-- 批17 17B：列显示配置（ID/名称/操作列恒显不进 defs）；描述=低频宽列默认隐 -->
          <span style="margin-right: var(--sp-2)"><ColumnSettings storage-key="cols.pool-main" :columns="poolColDefs" v-model:visible="poolVisible" /></span>
          <IconBtn :icon="MagicStick" :title="t('pool.backfillFactorBtn')" :disabled="navReadonly" @click="backfillFactor" />
          <IconBtn :icon="Plus" :title="t('pool.createTitle')" @click="showDialog = true" :disabled="navReadonly" />
        </div>
      </div>
    </template>
    <!-- 批17 17A：TableShell 列宽拖拽+持久化 -->
    <TableShell :data="pools" :row-key="r => r.id" :expand-row-keys="expanded" @expand-change="onExpand" storage-key="pool-main">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: var(--sp-2) 24px">
            <!-- 池深度数据回补（二档：财务/筹码/股东，非分钟） -->
            <el-button size="small" type="warning" style="margin-bottom: 10px"
                       :disabled="navReadonly" @click="backfillMinute(row)">{{ t('pool.backfillMinute') }}</el-button>
            <!-- 单标的添加 -->
            <div style="display: flex; gap: 8px; align-items: center; margin-top: var(--sp-2)">
              <el-input v-model="addSymbolInput[row.id]" :placeholder="t('pool.phAddSymbol')" style="width: 220px" size="small" />
              <IconBtn size="small" :icon="Plus" :title="t('pool.add')" @click="addSymbol(row.id)" />
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="id" label="ID" min-width="100" />
      <el-table-column prop="name" :label="t('common.name')" min-width="160" show-overflow-tooltip />
      <el-table-column v-if="colOn('category')" prop="category" :label="t('pool.category')" min-width="110" />
      <el-table-column v-if="colOn('symbol_count')" prop="symbol_count" :label="t('pool.symbolCount')" min-width="90" align="center">
        <template #default="{ row }">
          <el-badge :value="row.symbols?.length || 0" type="primary" />
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('description')" prop="description" min-width="220" show-overflow-tooltip :label="t('common.description')" />
      <el-table-column prop="actions" :label="t('common.action')" width="180">
        <template #default="{ row }">
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="editPool(row)" />
          <IconBtn size="small" :icon="Delete" :title="t('common.delete')" type="danger" @click="delPool(row)" />
        </template>
      </el-table-column>
    </TableShell>

    <el-dialog v-model="showDialog" :close-on-click-modal="false" :title="t('pool.createTitle')" width="560px">
      <el-form :model="newPool" label-width="110px">
        <el-form-item label="ID"><el-input v-model="newPool.id" :disabled="!!newPool._edit" /></el-form-item>
        <el-form-item :label="t('common.name')"><el-input v-model="newPool.name" /></el-form-item>
        <el-form-item :label="t('pool.category')">
          <el-select v-model="newPool.category" style="width: 100%">
            <el-option :label="t('pool.catAstock')" value="astock" />
            <el-option :label="t('pool.catConvertible')" value="convertible" />
            <el-option :label="t('pool.catEtf')" value="etf" />
            <el-option :label="t('pool.catCrypto')" value="crypto" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('pool.symbolList')">
          <el-select v-model="poolSymbols" multiple filterable allow-create default-first-option
            :placeholder="t('pool.phSymbols')" style="width: 100%" :reserve-keyword="true"
            :remote-method="searchSymbols" remote :loading="symbolLoading">
            <el-option v-for="sym in symbolOptions" :key="sym" :value="sym" :label="sym" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="newPool.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="savePool">{{ t('pool.createBtn') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, reactive, computed, onMounted, inject, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { getPools, createPoolApi, deletePoolApi } from '../api'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import { Plus, MagicStick, Edit, Delete } from '@element-plus/icons-vue'

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const pools = ref([])
const showDialog = ref(false)
const expanded = ref([])
const addSymbolInput = reactive({})
const newPool = ref({ id: '', name: '', category: 'astock', symbolsStr: '', description: '', _edit: false })

// 批17 17B：列显示配置——ID/名称/操作列恒显不进 defs；描述=低频宽列默认隐
const poolColDefs = computed(() => [
  { key: 'category', label: t('pool.category') },
  { key: 'symbol_count', label: t('pool.symbolCount') },
  { key: 'description', label: t('common.description'), hidden: true },
])
const poolVisible = ref([])
const colOn = k => poolVisible.value.includes(k)

const load = async () => {
  try { pools.value = await getPools() } catch (e) { ElMessage.error(t('pool.loadFailed')) }
}

const onExpand = (row, expandedRows) => {
  expanded.value = expandedRows.map(r => r.id)
}

const addSymbol = async (pid) => {
  const sym = (addSymbolInput[pid] || '').trim()
  if (!sym) return
  try {
    await api.post(`/pool/${pid}/symbol`, { symbol: sym })
    ElMessage.success(`${sym} ✓`)
    addSymbolInput[pid] = ''
    await load()
  } catch (e) {
    ElMessage.error(apiErr(e, t('common.saveFailed')))
  }
}

const removeSymbol = async (pid, sym) => {
  try {
    await api.delete(`/pool/${pid}/symbol/${sym}`)
    ElMessage.success(`${sym} ✕`)
    await load()
  } catch (e) {
    ElMessage.error(apiErr(e, t('common.deleteFailed')))
  }
}

const savePool = async () => {
  const np = newPool.value
  if (!np.id || !np.name) { ElMessage.warning(t('pool.idNameRequired')); return }
  try {
    // H3（01 P0#5，数据丢失级）：后端 PoolReq 契约只收 symbolsStr（\n 分隔）——此前发 symbols 数组被忽略，保存即清空池标的
    await createPoolApi({ id: np.id, name: np.name, category: np.category, description: np.description, symbolsStr: np.symbolsStr })
    ElMessage.success(t('common.saveSuccess'))
    showDialog.value = false
    newPool.value = { id: '', name: '', category: 'astock', symbolsStr: '', description: '', _edit: false }
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

const editPool = row => {
  newPool.value = {
    id: row.id, name: row.name, category: row.category || 'astock',
    symbolsStr: (row.symbols || []).join('\n'), description: row.description || '',
    _edit: true,
  }
  showDialog.value = true
}
const delPool = async row => {
  try { await deletePoolApi(row.id); ElMessage.success(t('common.deleteSuccess')); await load() } catch (e) { ElMessage.error(t('common.deleteFailed')) }
}

onMounted(() => { load() })

// P2-9(05 §5.9):remote 标的搜索
const poolSymbols = ref([])
const symbolOptions = ref([])
const symbolLoading = ref(false)
const searchSymbols = async (query) => {
  if (!query || query.length < 2) { symbolOptions.value = []; return }
  symbolLoading.value = true
  try {
    const results = await api.get(`/stock/search?q=${encodeURIComponent(query)}&limit=20`)
    symbolOptions.value = (results || []).map(r => r.symbol || r.ts_code || r)
  } catch { symbolOptions.value = [query] }
  finally { symbolLoading.value = false }
}
const backfillMinute = async (row) => {
  // 池深度数据回补（二档：财务/筹码/股东——pool_data，与分钟无关）
  try {
    await api.post(`/sync/pool-data/trigger?pool_id=${row.id}&full=true`)
    ElMessage.success(t('pool.backfillStarted', { name: row.name }))
  } catch { ElMessage.error(t('common.failed')) }
}
const backfillFactor = async () => {
  // 复权因子回补（历史缺口补齐）
  try {
    await api.post('/sync/adj-factor-backfill')
    ElMessage.success(t('common.success'))
  } catch { ElMessage.error(t('common.failed')) }
}
watch(() => showDialog.value, v => {
  if (v) poolSymbols.value = (newPool.value.symbolsStr || '').split('\n').map(x => x.trim()).filter(Boolean)
  else newPool.value.symbolsStr = poolSymbols.value.join('\n')
})

</script>
