<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>因子库</span>
        <el-button type="primary" size="small" @click="showDialog = true">新建因子</el-button>
      </div>
    </template>
    <el-table :data="factors" stripe>
      <el-table-column prop="name" label="名称" width="150" />
      <el-table-column prop="category" label="类别" width="100">
        <template #default="{ row }"><el-tag size="small">{{ row.category }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="description" label="描述" />
      <el-table-column label="参数" width="200">
        <template #default="{ row }">{{ JSON.stringify(row.params) }}</template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showDialog" title="新建 DSL 因子" width="600px">
      <el-form :model="newFactor" label-width="100px">
        <el-form-item label="因子名称"><el-input v-model="newFactor.name" /></el-form-item>
        <el-form-item label="适配品类">
          <el-select v-model="newFactor.category" style="width: 100%">
            <el-option label="趋势" value="trend" />
            <el-option label="均值回归" value="meanrev" />
            <el-option label="可转债" value="convertible" />
            <el-option label="加密" value="crypto" />
          </el-select>
        </el-form-item>
        <el-form-item label="DSL 表达式">
          <el-input v-model="newFactor.expr" type="textarea" :rows="3"
            placeholder="如: close / 10 - 1（支持 close/high/low/open_/volume + 算术运算 + abs/max/min/round）" />
          <div style="color: #999; font-size: 12px; margin-top: 4px">安全 eval，AST 白名单，禁 import/任意调用</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" @click="createFactor">创建</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'

const factors = ref([])
const showDialog = ref(false)
const newFactor = ref({ name: '', category: 'trend', expr: '' })

const load = () => {
  // 预置因子
  factors.value = [
    { name: 'ma_dev', category: 'trend', description: '均线偏离度', params: { n: 20 } },
    { name: 'rsi', category: 'trend', description: 'RSI', params: { n: 14 } },
    { name: 'volume_ratio', category: 'trend', description: '量比', params: { n: 5 } },
    { name: 'double_low', category: 'convertible', description: '可转债双低', params: {} },
    { name: 'funding_rate', category: 'crypto', description: '资金费率', params: {} },
    { name: 'dsl', category: 'custom', description: 'DSL 表达式因子', params: {} },
  ]
}
const createFactor = () => {
  // TODO: 调后端创建 DSL 因子
  ElMessage.success('DSL 因子创建（API 待接）')
  showDialog.value = false
}
onMounted(load)
</script>