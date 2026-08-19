<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('pool.manageTitle') }}</span>
        <el-button type="primary" @click="showDialog = true">{{ t('pool.createTitle') }}</el-button>
      </div>
    </template>
    <el-table :data="pools" stripe :row-key="r => r.id" :expand-row-keys="expanded" @expand-change="onExpand">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: 8px 24px">
            <!-- 覆盖状态（分钟历史池才显示） -->
            <template v-if="row.minute_history_start">
              <div v-if="minuteStatus[row.id]" style="margin-bottom: 12px">
                <el-table :data="minuteStatus[row.id]" size="small" max-height="300">
                  <el-table-column prop="symbol" :label="t('common.symbol')" width="160" />
                  <el-table-column :label="t('pool.minuteLastTs')" width="200">
                    <template #default="{ row: s }">{{ s.last_ts || '-' }}</template>
                  </el-table-column>
                  <el-table-column :label="t('pool.minuteCovered')" width="100">
                    <template #default="{ row: s }">
                      <el-tag :type="s.covered ? 'success' : 'warning'" size="small">
                        {{ s.covered ? '✓' : t('pool.pending') }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column :label="t('common.action')" width="80">
                    <template #default="{ row: s }">
                      <el-button type="danger" size="small" @click="removeSymbol(row.id, s.symbol)">✕</el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <div v-if="minuteProgress[row.id]" style="margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary)">
                  {{ t('pool.minuteSyncProgress') }}: {{ minuteProgress[row.id].synced || 0 }}
                  <span v-if="minuteProgress[row.id].pending"> / +{{ minuteProgress[row.id].pending }} {{ t('pool.pending') }}</span>
                </div>
              </div>
            </template>
            <!-- 单标的添加 -->
            <div style="display: flex; gap: 8px; align-items: center; margin-top: 8px">
              <el-input v-model="addSymbolInput[row.id]" :placeholder="t('pool.phAddSymbol')" style="width: 220px" size="small" />
              <el-button type="success" size="small" @click="addSymbol(row.id)">+</el-button>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="id" label="ID" width="130" />
      <el-table-column prop="name" :label="t('common.name')" width="160" />
      <el-table-column prop="category" :label="t('pool.category')" width="100" />
      <el-table-column :label="t('pool.symbolCount')" width="80" align="center">
        <template #default="{ row }">
          <el-badge :value="row.symbols?.length || 0" type="primary" />
        </template>
      </el-table-column>
      <el-table-column :label="t('pool.minuteStart')" width="120">
        <template #default="{ row }">
          <span v-if="row.minute_history_start" style="font-size: 12px">{{ row.minute_history_start }}</span>
          <span v-else style="color: var(--el-text-color-placeholder)">-</span>
        </template>
      </el-table-column>
      <el-table-column prop="description" :label="t('common.description')" min-width="150" />
      <el-table-column :label="t('common.action')" width="200">
        <template #default="{ row }">
          <el-button type="primary" size="small" @click="editPool(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" size="small" @click="delPool(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showDialog" :title="t('pool.createTitle')" width="520px">
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
        <el-form-item :label="t('pool.minuteStartLabel')">
          <el-date-picker v-model="newPool.minuteStart" type="date" style="width: 100%"
                          :placeholder="t('pool.phMinuteStart')" value-format="YYYY-MM-DD" clearable />
          <div style="font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px">
            {{ t('pool.minuteStartHint') }}
          </div>
        </el-form-item>
        <el-form-item :label="t('pool.symbolList')">
          <el-input v-model="newPool.symbolsStr" type="textarea" :rows="4" :placeholder="t('pool.phSymbols')" />
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
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { getPools, createPoolApi, deletePoolApi } from '../api'

const { t } = useI18n()
const pools = ref([])
const showDialog = ref(false)
const expanded = ref([])
const addSymbolInput = reactive({})
const minuteStatus = reactive({})   // {pool_id: [{symbol,last_ts,covered}]}
const minuteProgress = reactive({}) // {pool_id: {status,synced,pending}}
const newPool = ref({ id: '', name: '', category: 'astock', symbolsStr: '', description: '', minuteStart: null, _edit: false })

const load = async () => {
  try { pools.value = await getPools() } catch (e) { ElMessage.error(t('pool.loadFailed')) }
}

const onExpand = async (row, expandedRows) => {
  expanded.value = expandedRows.map(r => r.id)
  if (expandedRows.some(r => r.id === row.id) && row.minute_history_start) {
    await loadMinuteStatus(row.id)
  }
}

const loadMinuteStatus = async (pid) => {
  try {
    const r = await api.get(`/pool/${pid}/minute-status`)
    minuteStatus[pid] = r.symbols || []
  } catch { minuteStatus[pid] = [] }
}

const addSymbol = async (pid) => {
  const sym = (addSymbolInput[pid] || '').trim()
  if (!sym) return
  try {
    await api.post(`/pool/${pid}/symbol`, { symbol: sym })
    ElMessage.success(`${sym} ✓`)
    addSymbolInput[pid] = ''
    await load()
    if (minuteStatus[pid]) await loadMinuteStatus(pid)
  } catch (e) {
    ElMessage.error(e?.detail || t('common.saveFailed'))
  }
}

const removeSymbol = async (pid, sym) => {
  try {
    await api.delete(`/pool/${pid}/symbol/${sym}`)
    ElMessage.success(`${sym} ✕`)
    await load()
    if (minuteStatus[pid]) await loadMinuteStatus(pid)
  } catch (e) {
    ElMessage.error(e?.detail || t('common.deleteFailed'))
  }
}

const savePool = async () => {
  const np = newPool.value
  if (!np.id || !np.name) { ElMessage.warning(t('pool.idNameRequired')); return }
  try {
    const symbols = np.symbolsStr.split('\n').map(s => s.trim()).filter(Boolean)
    await createPoolApi({ id: np.id, name: np.name, category: np.category, description: np.description, symbols, minute_history_start: np.minuteStart })
    if (np.minuteStart) {
      // 已随 createPoolApi 一起提交（minute_history_start 字段）
    }
    ElMessage.success(t('common.saveSuccess'))
    showDialog.value = false
    newPool.value = { id: '', name: '', category: 'astock', symbolsStr: '', description: '', minuteStart: null, _edit: false }
    await load()
  } catch (e) { ElMessage.error(e?.detail || t('common.saveFailed')) }
}

const editPool = row => {
  newPool.value = {
    id: row.id, name: row.name, category: row.category || 'astock',
    symbolsStr: (row.symbols || []).join('\n'), description: row.description || '',
    minuteStart: row.minute_history_start || null, _edit: true,
  }
  showDialog.value = true
}
const delPool = async row => {
  try { await deletePoolApi(row.id); ElMessage.success(t('common.deleteSuccess')); await load() } catch (e) { ElMessage.error(t('common.deleteFailed')) }
}
onMounted(load)
</script>
