<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('analysis.title') }}</span>
        <el-button @click="load" :loading="loading">{{ t('analysis.refresh') }}</el-button>
      </div>
    </template>
    <el-table :data="results" stripe>
      <el-table-column prop="symbol" label="股票" width="120" />
      <el-table-column prop="score" :label="t('analysis.score')" width="100" sortable />
      <el-table-column :label="t('analysis.rating')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.rating === 'BUY' ? 'success' : row.rating === 'AVOID' ? 'danger' : 'info'">{{ row.rating }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="support" :label="t('analysis.support')" width="100" />
      <el-table-column prop="resistance" :label="t('analysis.resistance')" width="100" />
      <el-table-column prop="conclusion" :label="t('analysis.conclusion')" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" @click="addToPool(row)">加入池</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showPoolDialog" title="加入标的池" width="400px">
      <el-form label-width="80px">
        <el-form-item label="标的">{{ currentSymbol }}</el-form-item>
        <el-form-item label="选择池">
          <el-select v-model="poolTarget" style="width: 100%" placeholder="选择目标池">
            <el-option v-for="p in pools" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPoolDialog = false">取消</el-button>
        <el-button type="primary" @click="confirmAddPool" :loading="adding">加入</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAstockSelection, getPools, createPoolApi } from '../api'

const { t } = useI18n()
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
  if (!poolTarget.value) { ElMessage.warning('请选择池'); return }
  adding.value = true
  try {
    const pool = pools.value.find(p => p.id === poolTarget.value)
    if (!pool) throw new Error('池不存在')
    const symbols = [...(pool.symbols || []), currentSymbol.value].filter((v, i, a) => a.indexOf(v) === i)
    await createPoolApi({ id: pool.id, name: pool.name, category: pool.category, symbolsStr: symbols.join('\n'), description: pool.description })
    ElMessage.success(`${currentSymbol.value} 已加入 ${pool.name}`)
    showPoolDialog.value = false
  } catch (e) { ElMessage.error('加入失败') }
  finally { adding.value = false }
}

onMounted(async () => { await load(); await loadPools() })
</script>
