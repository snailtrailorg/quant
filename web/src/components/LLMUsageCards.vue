<template>
  <!-- 批50：LLM 用量监控卡（每模型一卡：今日调用/tokens/成功率+48h×小时调用曲线）——
       仿 SystemMetricsCards（VChart 按需+cssVar 解实色——批22 约定 echarts 不解析 CSS 变量；
       无阈值线/状态徽标=LLM 卡差异）；网格 auto-fill 自适应模型数 -->
  <div v-if="!models.length" class="empty-group">{{ t('llm.llmUsageEmpty') }}</div>
  <div v-else class="card-grid">
    <el-card v-for="m in models" :key="m.provider + m.model" shadow="never" class="metric-card">
      <div class="card-head">
        <span class="card-name">{{ m.model }}</span>
        <span class="card-prov">{{ m.provider }}</span>
      </div>
      <div class="card-value">
        <span class="num">{{ m.today.calls }}</span>
        <span class="sub">{{ t('llm.calls') }} · {{ m.today.tokens.toLocaleString() }} tk · {{ m.today.success_rate }}%</span>
      </div>
      <VChart :option="optionOf(m)" autoresize style="height: 128px" />
    </el-card>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { cssVar } from '../utils/cssVar'

use([CanvasRenderer, LineChart, GridComponent])

const { t } = useI18n()
defineProps({ models: { type: Array, default: () => [] } })

const optionOf = (m) => ({
  animation: false,
  grid: { left: 2, right: 4, top: 2, bottom: 20 },
  xAxis: {
    type: 'category', show: true,
    data: m.series.map(p => p.ts.slice(5, 13).replace('T', ' ')),   // MM-DD HH
    axisLine: { lineStyle: { color: cssVar('--border-weak') } },
    axisTick: { show: false },
    axisLabel: { show: true, color: cssVar('--text-secondary'), fontSize: 10,
                 hideOverlap: true, interval: 11 },   // 48 点≈每 12h 一刻度
    splitLine: { show: false },
  },
  yAxis: { type: 'value', show: false, min: 0 },
  series: [{
    type: 'line', showSymbol: false, data: m.series.map(p => p.calls),
    lineStyle: { width: 1.5, color: cssVar('--brand-600') },
    areaStyle: { color: cssVar('--brand-600'), opacity: 0.08 },   // 批50 盲审 A-P1-2：cssVar 单参无 fallback+--brand-fill-weak 令牌不存在→同色低透明零新令牌
  }],
})
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: var(--sp-3); }   /* 批50：模型数不定——自适应（方案盲审 B-P2-4） */
.metric-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.card-name { font-size: var(--fs-label); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-prov { font-size: var(--fs-foot); color: var(--text-secondary); flex-shrink: 0; margin-left: 8px; }
.card-value { margin-bottom: 4px; }
.num { font-family: var(--font-num); font-weight: 600; font-size: var(--fs-kpi); }
.sub { font-size: var(--fs-foot); color: var(--text-secondary); margin-left: 8px; }
.empty-group { color: var(--text-secondary); font-size: var(--fs-foot); padding: 18px 0; text-align: center; }
</style>
