<template>
  <div v-if="!models.length" class="empty-group">{{ t('llm.llmUsageEmpty') }}</div>
  <el-card v-else shadow="never" class="metric-card">
    <div class="head-row">
      <div class="chips">
        <span v-for="m in models" :key="m.provider + m.model" class="chip chip-btn"
              :class="{ off: !selected.has(m.model) }" @click="toggleSel(m.model)">
        <i class="dot" :style="{ background: colorOf(m) }" />
        <span class="chip-name">{{ m.model }}</span>
        <span class="chip-sub">{{ m.today.calls }} · {{ m.today.tokens.toLocaleString() }}tk · {{ m.today.success_rate }}%</span>
        </span>
      </div>
    </div>
    <VChart :option="option" autoresize style="height: 280px" />
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { cssVar } from '../utils/cssVar'
import { fmtTime } from '../utils/fmtTime'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent])

const props = defineProps({ models: { type: Array, default: () => [] } })
const { t } = useI18n()

// 批54 两档(用户裁定 B):hour=小时粒度窗 2 天/day=天粒度窗 30 天
// 批54 追加:模型筛选(用户裁定——chips 可点 toggle,轴范围随所选模型数据起点动态变化)
const selected = ref(new Set())
watch(() => props.models, (ms) => { selected.value = new Set(ms.map(m => m.model)) }, { immediate: true })
const toggleSel = (name) => {
  const s2 = new Set(selected.value)
  s2.has(name) ? s2.delete(name) : s2.add(name)
  if (s2.size) selected.value = s2   // 至少留一个(全取消=全选语义混乱)
}
const shown = computed(() => props.models.filter(m => selected.value.has(m.model)))

// 多模型调色板（cssVar 主色领衔+扩展色——暗色变体下仍可辨）
const PALETTE = [
  cssVar('--brand-600'), cssVar('--chart-c1'), cssVar('--chart-c2'),
  cssVar('--chart-c3'), cssVar('--chart-c4'), cssVar('--chart-c5'),
]
const colorOf = (m) => PALETTE[props.models.indexOf(m) % PALETTE.length]

const option = computed(() => {
  const grid = (shown.value[0] || props.models[0])?.series?.map(p => fmtTime.s(p.ts)) || []
  // 轴左界=所选模型最早**有活动**的点(非零 calls——筛选后时间范围跟着变)
  let minIdx = 0
  for (const m of shown.value) {
    const idx = m.series.findIndex(p => p.calls > 0)
    if (idx >= 0 && (minIdx === 0 || idx < minIdx)) minIdx = idx
  }
  const axisMin = minIdx > 0 ? grid[minIdx] : undefined
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { show: true, bottom: 0, textStyle: { color: cssVar('--text-secondary'), fontSize: 11 } },
    grid: { left: 8, right: 12, top: 8, bottom: 64 },
    xAxis: {
      type: 'category', show: true, data: grid, min: axisMin,
      axisLine: { lineStyle: { color: cssVar('--border-weak') } },
      axisTick: { show: false },
      axisLabel: { color: cssVar('--text-secondary'), fontSize: 10, hideOverlap: true, interval: 5 },
      splitLine: { show: false },
    },
    dataZoom: [   // 批54 追加三:单档——初始窗 30 天+滚轮/slider 回看全历史
      { type: 'inside', start: 100 - 30 / (grid.length || 1) * 100, end: 100 },
      { type: 'slider', height: 14, bottom: 22, start: 100 - 30 / (grid.length || 1) * 100, end: 100 },
    ],
    yAxis: { type: 'value', show: true,
             axisLabel: { color: cssVar('--text-secondary'), fontSize: 10 },
             splitLine: { lineStyle: { color: cssVar('--border-weak') } } },
    series: shown.value.map((m) => ({
      name: m.model, type: 'bar', stack: 'total', barMaxWidth: 18,
      data: m.series.map(p => p.calls),
      itemStyle: { color: colorOf(m) },
      emphasis: { focus: 'series' },   // 批54 追加:堆叠柱(计数数据柱形语义+多模型构成一眼看——用户裁定)
    })),
  }
})
</script>

<style scoped>
.metric-card { border: 1px solid var(--border-weak); }
.chips { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-bottom: 8px; }
.chip-btn { cursor: pointer; user-select: none; }
.chip-btn.off { opacity: .35; }
.chip { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.chip-name { font-size: var(--fs-label); font-weight: 600; }
.chip-sub { font-size: var(--fs-foot); color: var(--text-secondary); }
.empty-group { color: var(--text-secondary); font-size: var(--fs-foot); padding: 18px 0; text-align: center; }
</style>
