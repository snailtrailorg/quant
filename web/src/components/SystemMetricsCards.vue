<template>
  <!-- 批22：系统指标卡片组（内存/磁盘/swap）——每卡=状态徽标+当前值+sparkline+阈值线；
       30s 轮询（对齐旧 Health.vue 自动刷新——盲审 B-P1-2）；
       数据拉不到（无权限/空表）整组占位，不渲染假 0.0% 正常态（盲审 B-P2-2） -->
  <!-- 批24 结构统一：组标题从卡外裸 h4 归位 el-card header（用户管理式——批23 表头规则同构） -->
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>{{ t('sysmon.metrics') }}</span></div>
    </template>
    <div v-if="!hasData" class="empty-group">{{ t('sysmon.noData') }}</div>
    <div v-else class="card-grid">
      <el-card v-for="c in cards" :key="c.kind" shadow="never" class="metric-card">
        <div class="card-head">
          <span class="card-name">{{ c.label }}</span>
          <el-tag :type="c.status === 'critical' ? 'danger' : c.status === 'warning' ? 'warning' : 'success'" size="small" effect="light">{{ statusText(c.status) }}</el-tag>
        </div>
        <div class="card-value">
          <span class="pct" :class="c.status !== 'normal' ? c.status : ''">{{ (c.pct * 100).toFixed(1) }}%</span>
          <span class="bytes">{{ c.bytes }}</span>
        </div>
        <VChart v-if="c.series.length" :option="c.option" autoresize style="height: 128px" />
        <div v-else class="empty-spark">{{ t('sysmon.noData') }}</div>
      </el-card>
    </div>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, MarkLineComponent, MarkAreaComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getSystemMetrics, getSystemAlerts } from '../api'
import { cssVar } from '../utils/cssVar'

use([CanvasRenderer, LineChart, GridComponent, MarkLineComponent, MarkAreaComponent])   // 批78：MarkArea=日期交替带（不注册=tree-shaking 静默不渲染）

const { t } = useI18n()
const metrics = ref({})
const alerts = ref([])
let pollTimer = null
let lastSig = ''   // 数据未变不赋值——曲线/数值零重绘，轮询视觉连续（用户裁定：刷新不突兀）

const fmtBytes = b => {
  if (!b) return '—'
  const gb = b / (1024 ** 3)
  return gb >= 1 ? `${gb.toFixed(1)}G` : `${(b / (1024 ** 2)).toFixed(0)}M`
}
const statusText = s => s === 'critical' ? t('sysmon.critical') : s === 'warning' ? t('sysmon.warning') : t('sysmon.normal')

const hasData = computed(() => !!(metrics.value.resources && Object.keys(metrics.value.resources).length))

const defs = [
  // 批78（用户裁定）：CPU 前置——卡序 CPU→内存→交换→磁盘（批 28-6「内存起步」序由本批修正）
  { kind: 'cpu', label: () => t('sysmon.cpu') },
  { kind: 'mem', label: () => t('sysmon.mem') },
  { kind: 'swap', label: () => t('sysmon.swap') },
  { kind: 'disk', label: () => t('sysmon.disk') },
]

// 批78：日期交替背景带——按序列 local 日期边界切区间，隔日着色（日期界限视觉清晰）
const dayBands = (data) => {
  const bands = []
  let dayKey = null, start = null, end = null
  const flush = () => { if (dayKey !== null) bands.push([start, end]) }
  for (const p of data) {
    const d = new Date(p[0])
    const k = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
    if (k !== dayKey) { flush(); dayKey = k; start = p[0]; end = p[0] }
    else end = p[0]
  }
  flush()
  return bands.filter((_, i) => i % 2 === 1)   // 第 0 日透明起，隔日一带
}

const cards = computed(() => {
  const res = metrics.value.resources || {}
  const thr = metrics.value.thresholds || {}
  const series = metrics.value.series || {}
  return defs.map(({ kind, label }) => {
    const cur = res[kind]
    const pct = cur ? cur.pct : 0
    const rule = { mem: 'mem_high', disk: 'disk_high', swap: 'swap_high', cpu: 'cpu_high' }[kind]
    const a = alerts.value.find(x => x.rule_id === rule)
    const status = a ? (a.severity === 'critical' ? 'critical' : 'warning') : 'normal'
    const ts = thr[kind] || {}
    // 批78：曲线画 FIR 滤波值（5 分钟矩形窗均值落库）——used_avg 缺（历史行/未算出）回退原值；
    // cpu 无字节语义（pct 本身 0-1），不走 used/total 共享除法（无 total 恒 0 平线陷阱）
    const mapSeries = p => {
      const x = new Date(p.ts).getTime()
      const v = p.used_avg ?? p.used
      return [x, kind === 'cpu' ? v : (p.total ? v / p.total : 0)]
    }
    const s = (series[kind] || []).map(mapSeries)
    // echarts canvas（zrender）不解析 CSS 变量——色值经 cssVar 解实值（盲审 B-P1-1，Dashboard 同款约定）
    const lineColor = status === 'critical' ? cssVar('--critical')
      : status === 'warning' ? cssVar('--warn-fill') : cssVar('--brand-600')
    const option = {
      animation: false,   // 关动画：轮询更新时曲线瞬切不滚动（用户裁定）
      grid: { left: 2, right: 4, top: 2, bottom: 20 },   // 底部留刻度空间（用户裁定：显示时间刻度）
      xAxis: {
        type: 'time', show: true,
        minInterval: 24 * 3600 * 1000,   // 批78：刻度钉日界（防自动落 12h 档致同日双签「09-21 09-21」并列）
        axisLine: { lineStyle: { color: cssVar('--border-weak') } },
        axisTick: { show: false },
        axisLabel: { show: true, color: cssVar('--text-secondary'), fontSize: 10,
                     hideOverlap: true,   // 刻度自动按采集周期密度避让（内存60s密/磁盘1h疏）
                     formatter: '{MM}-{dd}' },   // 批78（用户需求）：纯日期标签——日界由交替背景带承担
        splitLine: { show: false },
      },
      yAxis: { type: 'value', show: false, min: 0, max: 1 },
      series: [{
        type: 'line', data: s, showSymbol: false, smooth: true,
        lineStyle: { width: 2, color: lineColor },
        areaStyle: { opacity: 0.08, color: lineColor },
        markArea: { silent: true,   // 批78：日期交替带（隔日一块浅色）
          itemStyle: { color: cssVar('--el-fill-color-light') },
          data: dayBands(s).map(([a, b]) => [{ xAxis: a }, { xAxis: b }]) },
        markLine: { silent: true, symbol: 'none', label: { show: false },
          lineStyle: { type: 'dashed', color: cssVar('--text-secondary') },
          data: [ts.warn ? { yAxis: ts.warn } : null, ts.crit ? { yAxis: ts.crit } : null].filter(Boolean) },
      }],
    }
    return { kind, label: label(), pct, status,
             bytes: kind === 'cpu' || !cur ? '—' : `${fmtBytes(cur.used)} / ${fmtBytes(cur.total)}`,
             series: s, option }
  })
})

const load = async () => {
  try {
    const m = await getSystemMetrics()
    const sig = JSON.stringify([m.resources, m.thresholds, m.series])
    if (sig !== lastSig) { lastSig = sig; metrics.value = m }
  } catch {}
  try {
    const a = (await getSystemAlerts()).items || []
    const sig = JSON.stringify(a)
    if (sig !== alertsSig) { alertsSig = sig; alerts.value = a }
  } catch {}
}
let alertsSig = ''
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.empty-group { color: var(--text-secondary); font-size: var(--fs-label); padding: var(--sp-6) 0; text-align: center; }
.card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-3); }   /* 批78（用户裁定）：固定 4 列各占 1/4（批51「固定 3 列」随批更新；CPU 卡加入后 4 张一行满） */
.metric-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-2); }
.card-name { font-size: var(--fs-label); color: var(--text-secondary); }
.card-value { display: flex; align-items: baseline; gap: var(--sp-2); margin-bottom: var(--sp-2); }
.pct { font-family: var(--font-num); font-size: var(--fs-page); font-weight: 600; }
.pct.critical { color: var(--critical); }
.pct.warning { color: var(--warn-fill); }
.bytes { font-family: var(--font-num); font-size: var(--fs-foot); color: var(--text-secondary); }
.empty-spark { height: 128px; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); font-size: var(--fs-foot); }
</style>
