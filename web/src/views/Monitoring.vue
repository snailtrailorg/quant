<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('monitoring.title') }}</span>
        <el-button type="primary" @click="load">{{ t('common.refresh') }}</el-button>
      </div>
    </template>
    <el-row :gutter="16" v-loading="loading">
      <el-col :span="12" v-for="s in strategies" :key="s.id" style="margin-bottom: 16px">
        <el-card shadow="hover">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ s.name }}</span>
              <el-tag :type="s.enabled ? 'success' : 'info'">
                {{ s.enabled ? t('strategy.statusRunning') : t('strategy.statusStopped') }}
              </el-tag>
            </div>
          </template>
          <div style="margin-bottom: 12px">
            <el-descriptions :column="2" border>
              <el-descriptions-item :label="t('common.symbol')">{{ s.symbol }}</el-descriptions-item>
              <el-descriptions-item :label="t('common.type')">{{ s.type }}</el-descriptions-item>
              <el-descriptions-item :label="t('monitoring.backtestVerified')">{{ s.backtest_verified ? '✓' : '✗' }}</el-descriptions-item>
              <el-descriptions-item :label="t('monitoring.equity')">—</el-descriptions-item>
            </el-descriptions>
          </div>
          <div style="height: 200px">
            <v-chart v-if="s._curve?.length" :option="chartOption(s)" autoresize style="height: 200px" />
            <div v-else style="text-align: center; color: #999; line-height: 200px">{{ t('monitoring.noEquityHint') }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <el-alert v-if="!strategies.length" type="info" :closable="false">{{ t('monitoring.noStrategy') }}</el-alert>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getStrategies, getPnl } from '../api'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

const { t } = useI18n()
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const strategies = ref([])
const loading = ref(false)
const formatNum = (n) => (n || 0).toFixed(0)
const chartOption = (s) => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 10, top: 10, bottom: 20 },
  xAxis: { type: 'category', data: (s._curve || []).map(c => c.ts?.slice(5, 10)) },
  yAxis: { type: 'value', scale: true },
  series: [{ type: 'line', data: (s._curve || []).map(c => c.value), smooth: true, lineStyle: { width: 2 }, areaStyle: { opacity: 0.1 } }],
})

const load = async () => {
  loading.value = true
  try {
    strategies.value = await getStrategies()
    // H6（01 P0#1）：原把单账户级 curve/total_value 塞进每个任务卡——每卡曲线实为同一条,误导。
    // 先隐藏(移除共用数据注入);每任务独立曲线待 per-task pnl 端点(P4)后恢复
  } finally { loading.value = false }
}
onMounted(load)
</script>
