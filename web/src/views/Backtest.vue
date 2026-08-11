<template>
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>回测运行列表</span>
          <el-button type="primary" @click="showForm = true">新建回测</el-button>
        </div>
      </template>
      <el-table :data="runs" stripe v-loading="loading" @row-click="goDetail">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="strategy_id" label="策略" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="mode" label="模式" width="80" />
        <el-table-column label="标的" min-width="100">
          <template #default="{ row }">{{ row.symbols?.length || 0 }} 个</template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ row.created_at?.slice(0, 19) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click.stop="goDetail(row)">详情</el-button>
            <el-button size="small" v-if="row.status === 'running'" type="danger" @click.stop="cancelRun(row)">终止</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建回测弹窗 -->
    <el-dialog v-model="showForm" title="新建回测" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="策略">
          <el-select v-model="form.strategyId" placeholder="选择策略" style="width: 100%">
            <el-option v-for="s in strategies" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标的池">
          <el-select v-model="form.poolId" placeholder="选择标的池" clearable style="width: 100%">
            <el-option v-for="p in pools" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="回测区间">
          <el-date-picker v-model="form.dateRange" type="daterange" start-placeholder="开始" end-placeholder="结束" style="width: 100%" />
        </el-form-item>
        <el-form-item label="模式">
          <el-select v-model="form.mode" style="width: 100%">
            <el-option label="并行" value="parallel" />
            <el-option label="串行" value="serial" />
            <el-option label="单只" value="single" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number v-model="form.capital" :min="10000" :step="100000" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="submitRun" :loading="submitting">开始回测</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getBacktests, createBacktest, getStrategies, getPools } from '../api'
import api from '../api'

const router = useRouter()
const runs = ref([])
const strategies = ref([])
const pools = ref([])
const loading = ref(false)
const submitting = ref(false)
const showForm = ref(false)
const form = ref({ strategyId: '', poolId: '', dateRange: null, mode: 'parallel', capital: 1000000 })

const statusType = (s) => ({ running: 'warning', done: 'success', error: 'danger', pending: 'info' }[s] || 'info')

const loadRuns = async () => {
  loading.value = true
  try { runs.value = await getBacktests() } catch (e) { ElMessage.error('加载回测列表失败') }
  finally { loading.value = false }
}

const goDetail = (row) => router.push(`/backtest/${row.id}`)

const cancelRun = async (row) => {
  // 终止回测（terminate_task 端点）
  try {
    await api.post(`/tasks/${row.task_id}/terminate`)
    ElMessage.success('已终止')
    await loadRuns()
  } catch (e) { ElMessage.error('终止失败') }
}

const submitRun = async () => {
  if (!form.value.strategyId) { ElMessage.warning('请选择策略'); return }
  submitting.value = true
  try {
    const payload = {
      strategy_config_id: form.value.strategyId,
      pool_id: form.value.poolId || null,
      mode: form.value.mode,
      params: {
        capital: form.value.capital,
        commission: 0.0005,
        start: form.value.dateRange?.[0]?.toISOString().slice(0, 10),
        end: form.value.dateRange?.[1]?.toISOString().slice(0, 10),
      },
    }
    await createBacktest(payload)
    ElMessage.success('已提交回测')
    showForm.value = false
    await loadRuns()
  } catch (e) { ElMessage.error('提交失败') }
  finally { submitting.value = false }
}

onMounted(async () => {
  strategies.value = await getStrategies()
  try { pools.value = await getPools() } catch (e) {}
  await loadRuns()
})
</script>