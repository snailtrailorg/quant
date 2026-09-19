<template>
  <div v-if="!models.length" class="empty-group">{{ t('llm.llmUsageEmpty') }}</div>
  <el-card v-else shadow="never" class="metric-card">
    <div class="head-row">
      <div class="chips">
        <span v-for="m in models" :key="m.provider + m.model" class="chip">
        <i class="dot" :style="{ background: colorOf(m) }" />
        <span class="chip-name">{{ m.model }}</span>
        <span class="chip-sub">{{ m.today.calls }} · {{ m.today.tokens.toLocaleString() }}tk · {{ m.today.success_rate }}%</span>
        </span>
      </div>
      <el-radio-group v-model="gran" size="small" @change="reload">
        <el-radio-button value="hour">{{ t('llm.granHour') }}</el-radio-button>
        <el-radio-button value="day">{{ t('llm.granDay') }}</el-radio-button>
      </el-radio-group>
    </div>
    <VChart :option="option" autoresize style="height: 280px" />
  </el-card>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { cssVar } from '../utils/cssVar'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent])

const props = defineProps({ models: { type: Array, default: () => [] } })
const { t } = useI18n()

// 批54 两档(用户裁定 B):hour=小时粒度窗 2 天(48/168 点)/day=天粒度窗 30 天(30/90 点)
const gran = defineModel('gran', { default: 'hour' })   // 批54:父级 v-model(刷新保档)
const emit = defineEmits(['reload'])
const reload = () => emit('reload', gran.value)

// 多模型调色板（cssVar 主色领衔+扩展色——暗色变体下仍可辨）
const PALETTE = [
  cssVar('--brand-600'), cssVar('--chart-c1'), cssVar('--chart-c2'),
  cssVar('--chart-c3'), cssVar('--chart-c4'), cssVar('--chart-c5'),
]
const colorOf = (m) => PALETTE[props.models.indexOf(m) % PALETTE.length]

const option = computed(() => {
  const grid = props.models[0]?.series?.map(p => p.ts.slice(5, 13).replace('T', ' ')) || []
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { show: true, bottom: 0, textStyle: { color: cssVar('--text-secondary'), fontSize: 11 } },
    grid: { left: 8, right: 12, top: 8, bottom: 64 },
    xAxis: {
      type: 'category', show: true, data: grid,
      axisLine: { lineStyle: { color: cssVar('--border-weak') } },
      axisTick: { show: false },
      axisLabel: { color: cssVar('--text-secondary'), fontSize: 10, hideOverlap: true, interval: 5 },
      splitLine: { show: false },
    },
    dataZoom: [   // 批54:初始窗(hour 48 点/day 30 点)+滚轮/slider 回看历史
      { type: 'inside', start: gran.value === 'hour' ? 100 - 48 / (grid.length || 1) * 100 : 100 - 30 / (grid.length || 1) * 100, end: 100 },
      { type: 'slider', height: 14, bottom: 22,
        start: gran.value === 'hour' ? 100 - 48 / (grid.length || 1) * 100 : 100 - 30 / (grid.length || 1) * 100, end: 100 },
    ],
    yAxis: { type: 'value', show: true,
             axisLabel: { color: cssVar('--text-secondary'), fontSize: 10 },
             splitLine: { lineStyle: { color: cssVar('--border-weak') } } },
    series: props.models.map((m, i) => ({
      name: m.model, type: 'line', showSymbol: false,
      data: m.series.map(p => p.calls),
      lineStyle: { width: 1.6 },
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      emphasis: { focus: 'series' },
    })),
  }
})
</script>

<style scoped>
.metric-card { border: 1px solid var(--border-weak); }
.chips { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-bottom: 8px; }
.chip { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.chip-name { font-size: var(--fs-label); font-weight: 600; }
.chip-sub { font-size: var(--fs-foot); color: var(--text-secondary); }
.empty-group { color: var(--text-secondary); font-size: var(--fs-foot); padding: 18px 0; text-align: center; }
</style>
