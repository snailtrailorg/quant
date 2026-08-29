<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('analysis.title') }}</span>
        <el-button type="primary" @click="load" :loading="loading">{{ t('analysis.refresh') }}</el-button>
      </div>
    </template>
    <el-table :data="results" stripe>
      <el-table-column prop="symbol" :label="t('analysis.stock')" width="120" />
      <el-table-column prop="score" :label="t('analysis.score')" width="100" sortable />
      <el-table-column :label="t('analysis.rating')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.rating === 'BUY' ? 'success' : row.rating === 'AVOID' ? 'danger' : 'info'">{{ row.rating }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="support" :label="t('analysis.support')" width="100" />
      <el-table-column prop="resistance" :label="t('analysis.resistance')" width="100" />
      <el-table-column prop="conclusion" :label="t('analysis.conclusion')">
        <template #default="{ row }">{{ (row.conclusion || '').replace(/=缺/g, '=—') || '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="200">
        <template #default="{ row }">
          <el-button type="primary" @click="addToPool(row)">{{ t('pool.add') }}</el-button>
          <el-button type="primary" @click="gotoDetail(row.symbol)">{{ t('common.detail') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showPoolDialog" :title="t('analysis.addPoolTitle')" width="400px">
      <el-form label-width="80px">
        <el-form-item :label="t('common.symbol')">{{ currentSymbol }}</el-form-item>
        <el-form-item :label="t('analysis.selectPool')">
          <el-select v-model="poolTarget" style="width: 100%" :placeholder="t('analysis.phPool')">
            <el-option v-for="p in pools" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="showPoolDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="confirmAddPool" :loading="adding">{{ t('analysis.addBtn') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAstockSelection, getPools, createPoolApi } from '../api'

const { t } = useI18n()
const router = useRouter()
const gotoDetail = symbol => router.push(`/stock/${symbol}`)
const results = ref([])
const loading = ref(false)
const pools = ref([])
const showPoolDialog = ref(false)
const poolTarget = ref('')
const currentSymbol = ref('')
const adding = ref(false)

const load = async () => {
  loading.value = true
  try { results.value = await getAstockSelection('') } finally { loading.value = false }
}
const loadPools = async () => { try { pools.value = await getPools() } catch (e) {} }

const addToPool = (row) => {
  currentSymbol.value = row.vt_symbol || row.symbol
  poolTarget.value = ''
  showPoolDialog.value = true
}

const confirmAddPool = async () => {
  if (!poolTarget.value) { ElMessage.warning(t('analysis.selectPoolWarn')); return }
  adding.value = true
  try {
    const pool = pools.value.find(p => p.id === poolTarget.value)
    if (!pool) throw new Error(t('analysis.poolNotExist'))
    const symbols = [...(pool.symbols || []), currentSymbol.value].filter((v, i, a) => a.indexOf(v) === i)
    await createPoolApi({ id: pool.id, name: pool.name, category: pool.category, symbolsStr: symbols.join('\n'), description: pool.description })
    ElMessage.success(t('analysis.addedTo', { symbol: currentSymbol.value, name: pool.name }))
    showPoolDialog.value = false
  } catch (e) { ElMessage.error(t('analysis.addFailed')) }
  finally { adding.value = false }
}

onMounted(async () => { await load(); await loadPools() })
</script>
