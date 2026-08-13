<template>
  <el-card v-loading="loading">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>回测详情 ({{ $route.params.id }})</span>
        <el-button @click="$router.back()">返回</el-button>
        <el-button type="success" size="small" @click="markVerified" :disabled="!run.strategy_config_id">标记回测验证（可切实盘）</el-button>
      </div>
    </template>

    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总收益</div><div class="value">{{ run.total_return_pct ?? '-' }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">胜率</div><div class="value">{{ run.win_rate ?? '-' }}%</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">夏普</div><div class="value">{{ run.sharpe_ratio ?? '-' }}</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">最大回撤</div><div class="value">{{ run.max_drawdown_pct ?? '-' }}%</div></div></el-card></el-col>
    </el-row>

    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>标的回测结果</span>
          <el-button size="small" @click="loadSummary" :loading="summaryLoading">组汇总（平均+排名）</el-button>
        </div>
      </template>
      <el-alert v-if="summary" type="info" :closable="false" style="margin-bottom: 12px">
        组平均：收益 {{ summary.avg?.total_return_pct }}% | 胜率 {{ summary.avg?.win_rate }}% | 夏普 {{ summary.avg?.sharpe_ratio }} | 共 {{ summary.count }} 只
      </el-alert>
      <el-table :data="symbols" stripe @row-click="goView">
        <el-table-column prop="symbol" label="标的" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">
            <el-tag :type="row.status === 'done' ? 'success' : 'warning'">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="收益" min-width="120">
          <template #default="{ row }">{{ row.result?.total_return_pct }}%</template>
        </el-table-column>
        <el-table-column label="夏普" min-width="100">
          <template #default="{ row }">{{ row.result?.sharpe_ratio }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" @click.stop="goView(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getBacktestRun, verifyStrategy } from '../api'
import api from '../api'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const run = ref({})
const symbols = ref([])
const summary = ref(null)
const summaryLoading = ref(false)

const goView = (row) => router.push(`/backtest/${route.params.id}/view/${row.symbol}`)
const loadSummary = async () => {
  summaryLoading.value = true
  try { summary.value = await api.get(`/backtest/${route.params.id}/summary`) } catch (e) { ElMessage.error('加载汇总失败') }
  finally { summaryLoading.value = false }
}

const markVerified = async () => {
  try {
    await verifyStrategy(run.value.strategy_config_id)
    ElMessage.success('已标记回测验证，策略可切实盘（第三级开关）')
  } catch (e) { ElMessage.error('标记失败') }
}
onMounted(async () => {
  loading.value = true
  try {
    const data = await getBacktestRun(route.params.id)
    run.value = data
    symbols.value = data.symbols || []
  } catch (e) { ElMessage.error('加载回测详情失败') }
  finally { loading.value = false }
})
</script>

<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>