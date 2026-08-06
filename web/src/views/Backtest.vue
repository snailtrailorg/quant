<template>
  <el-card>
    <template #header>回测中心</template>
    <el-form :model="form" label-width="120px">
      <el-form-item label="选择策略">
        <el-select v-model="form.strategyId" placeholder="选择策略" style="width: 300px">
          <el-option v-for="s in strategies" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="回测区间">
        <el-date-picker v-model="form.dateRange" type="daterange" start-placeholder="开始" end-placeholder="结束" style="width: 300px" />
      </el-form-item>
      <el-form-item label="初始资金">
        <el-input-number v-model="form.capital" :min="10000" :step="100000" style="width: 200px" />
      </el-form-item>
      <el-form-item label="手续费率">
        <el-input-number v-model="form.commission" :min="0" :step="0.0001" :precision="4" style="width: 200px" />
      </el-form-item>
      <el-form-item label="每笔股数">
        <el-input-number v-model="form.shares" :min="100" :step="100" style="width: 200px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="runBacktest" :loading="loading">开始回测</el-button>
      </el-form-item>
    </el-form>

    <el-divider v-if="result" />
    <div v-if="result">
      <el-row :gutter="20" style="margin-bottom: 20px">
        <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总收益</div><div class="value">{{ result.total_return_pct }}%</div></div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">最大回撤</div><div class="value">{{ result.max_drawdown_pct }}%</div></div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">夏普比率</div><div class="value">{{ result.sharpe_ratio }}</div></div></el-card></el-col>
        <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">交易次数</div><div class="value">{{ result.total_trades }}</div></div></el-card></el-col>
      </el-row>
      <el-card>
        <template #header>交易明细</template>
        <el-table :data="result.trades" stripe max-height="400">
          <el-table-column prop="ts" label="时间" width="180" />
          <el-table-column prop="action" label="方向" width="80" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="price" label="价格" width="100" />
          <el-table-column prop="commission" label="佣金" width="100" />
        </el-table>
      </el-card>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" style="margin-top: 20px" />
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getStrategies } from '../api'

const strategies = ref([])
const loading = ref(false)
const result = ref(null)
const error = ref('')
const form = ref({
  strategyId: '', dateRange: null, capital: 1000000,
  commission: 0.0005, shares: 1000,
})

onMounted(async () => { strategies.value = await getStrategies() })

const runBacktest = async () => {
  if (!form.value.strategyId) { ElMessage.warning('请选择策略'); return }
  loading.value = true; result.value = null; error.value = ''
  try {
    // TODO: 调 POST /api/backtest（后端端点待加）
    ElMessage.info('回测 API 待实现，后端 BacktestEngine 已就绪')
  } catch (e) { error.value = e.detail || e.message || '回测失败' }
  finally { loading.value = false }
}
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>