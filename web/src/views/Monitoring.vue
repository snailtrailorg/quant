<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>多策略实时监控看板</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
    <el-row :gutter="16" v-loading="loading">
      <el-col :span="12" v-for="s in strategies" :key="s.id" style="margin-bottom: 16px">
        <el-card shadow="hover">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ s.name }}</span>
              <el-tag :type="s.enabled ? 'success' : 'info'" size="small">
                {{ s.enabled ? '运行中' : '已停' }}
              </el-tag>
            </div>
          </template>
          <div style="margin-bottom: 12px">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="标的">{{ s.symbol }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ s.type }}</el-descriptions-item>
              <el-descriptions-item label="回测验证">{{ s.backtest_verified ? '✓' : '✗' }}</el-descriptions-item>
              <el-descriptions-item label="标的池">{{ s.pool_id || '未绑定' }}</el-descriptions-item>
            </el-descriptions>
          </div>
          <div ref="charts" style="height: 200px"></div>
          <div v-if="!s.enabled" style="text-align: center; color: #999; line-height: 200px">策略未运行</div>
        </el-card>
      </el-col>
    </el-row>
    <el-alert v-if="!strategies.length" type="info" :closable="false">暂无运行中策略</el-alert>
  </el-card>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { getStrategies } from '../api'

const strategies = ref([])
const loading = ref(false)
const charts = ref([])

const load = async () => {
  loading.value = true
  try {
    strategies.value = await getStrategies()
    await nextTick()
    // 渲染图表
    strategies.value.forEach((s, i) => {
      const el = charts.value[i]
      if (!el) return
      const chart = echarts.init(el)
      // 占位：实盘数据接入后用真实 K 线
      chart.setOption({
        xAxis: { type: 'category', data: ['--','--','--','--','--'] },
        yAxis: { type: 'value' },
        series: [{ type: 'line', data: [0,0,0,0,0], smooth: true }],
        tooltip: { trigger: 'axis' },
        grid: { left: 40, right: 10, top: 10, bottom: 20 },
      })
    })
  } finally { loading.value = false }
}
onMounted(load)
</script>
