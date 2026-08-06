<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>标的池管理</span>
        <el-button type="primary" size="small" @click="showDialog = true">新建标的池</el-button>
      </div>
    </template>
    <el-table :data="pools" stripe>
      <el-table-column prop="id" label="ID" width="150" />
      <el-table-column prop="name" label="名称" width="200" />
      <el-table-column prop="category" label="品类" width="120" />
      <el-table-column label="标的数量" width="100">
        <template #default="{ row }">{{ row.symbols?.length || 0 }}</template>
      </el-table-column>
      <el-table-column prop="description" label="描述" />
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button size="small" @click="editPool(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="delPool(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showDialog" title="新建标的池" width="500px">
      <el-form :model="newPool" label-width="80px">
        <el-form-item label="ID"><el-input v-model="newPool.id" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="newPool.name" /></el-form-item>
        <el-form-item label="品类">
          <el-select v-model="newPool.category" style="width: 100%">
            <el-option label="A股" value="astock" />
            <el-option label="可转债" value="convertible" />
            <el-option label="ETF" value="etf" />
            <el-option label="加密永续" value="crypto" />
          </el-select>
        </el-form-item>
        <el-form-item label="标的列表">
          <el-input v-model="newPool.symbolsStr" type="textarea" :rows="4" placeholder="一行一个 vt_symbol，如 600000.SHSE" />
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="newPool.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" @click="createPool">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getStrategies } from '../api'

const pools = ref([])
const showDialog = ref(false)
const newPool = ref({ id: '', name: '', category: 'astock', symbolsStr: '', description: '' })

const load = async () => {
  // TODO: 调 GET /api/pool
  pools.value = [
    { id: 'astock-pool', name: 'A股核心池', category: 'astock', symbols: ['600000.SHSE'], description: '核心A股标的' },
    { id: 'conv-pool', name: '可转债池', category: 'convertible', symbols: ['128044.SZSE','110092.SHSE'], description: '可转债轮动池' },
    { id: 'crypto-pool', name: '加密永续池', category: 'crypto', symbols: ['BTCUSDT-PERP.BINANCE'], description: 'BTC/ETH永续' },
  ]
}
const createPool = async () => {
  // TODO: 调 POST /api/pool
  ElMessage.success('标的池创建（API 待接）')
  showDialog.value = false
}
const editPool = row => ElMessage.info('编辑待实现')
const delPool = row => { pools.value = pools.value.filter(p => p.id !== row.id); ElMessage.success('已删除') }
onMounted(load)
</script>