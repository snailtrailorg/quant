<script setup>
// wd-20 §2.4 · KpiCard 共享组件（以 Dashboard .kpi-cell 为基准抽取——两页首屏同构）
// props: label(灰小字) / value(大数字) / sub?(下行说明) / trend?(+1.24/-0.5 带色带号) / spark?(number[])
const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], required: true },
  sub: { type: String, default: '' },
  trend: { type: Number, default: null },
  spark: { type: Array, default: null },
  tone: { type: String, default: '' },   // 额外类（如 up/down）
})
</script>
<template>
  <div class="kpi-cell"><el-card shadow="never"><div class="kpi">
    <div class="klabel">{{ label }}</div>
    <div class="kpi-num" :class="tone">{{ value }}</div>
    <svg v-if="spark && spark.length > 1" class="sparkline-svg" width="100%" height="24" viewBox="0 0 100 24">
      <polyline :points="sparkPoints" fill="none" stroke="var(--up)" stroke-width="1.5" />
    </svg>
    <div v-if="sub || trend != null" class="ksub">
      <span v-if="trend != null" :class="trend >= 0 ? 'up' : 'down'">{{ trend >= 0 ? '▲' : '▼' }} {{ Math.abs(trend) }}%</span>
      <span v-if="sub">{{ sub }}</span>
    </div>
  </div></el-card></div>
</template>
<script>
export default {
  computed: {
    sparkPoints() {
      const vals = this.spark || []
      if (vals.length < 2) return ''
      const min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1
      return vals.map((v, i) => `${i * 100 / (vals.length - 1)},${24 - (v - min) / range * 20}`).join(' ')
    },
  },
}
</script>
<style scoped>
.kpi-cell { flex: 1 1 0; min-width: 0; }
.kpi { padding: 6px 0; }
.klabel { color: var(--text-secondary); font-size: var(--fs-label); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kpi-num { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ksub { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: 2px; }
</style>
