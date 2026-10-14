<template>
  <!-- 批 120 线③：安全态势 Tab（五区布局 A~E，任务文件钉死）。
       数据两请求：snapshot（默认今日）+ history（30 天）；轮询 5min（每日数据，无需 ws）。
       批 124 美化（B 档）：A 区四卡化（同 SystemMetricsCards card-grid 形态——wd-20「首屏同构」）；
       数字走 --font-num；漏洞>0 用 --critical 配警示图标（tokens.css:48 语义色立法）；
       D 区分层 tag（批 122 暴露面分层的视觉表达）；E 区头部 30 天迷你条形图（复刻
       sparkline 模式：cssVar 取色+animation:false）。五区布局/StatusTag/TableShell/
       AI 纯文本不动。 -->
  <div v-if="loadFailed" class="sec-empty">
    <el-alert :type="isNoSnapshot ? 'info' : 'error'" :closable="false">
      {{ isNoSnapshot ? t('secpage.noSnapshot') : t('secpage.loadFailed') }}
    </el-alert>
  </div>
  <template v-else-if="snap">
    <!-- A 顶部状态卡组：漏洞总数+来源分色 / status / 扫描时间 / 基线偏差（四卡同构） -->
    <el-card>
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.postureTitle') }}</span>
          <span v-if="stale" class="stale-warn">{{ t('secpage.stale') }}</span>
        </div>
      </template>
      <div class="card-grid">
        <el-card shadow="never" class="metric-card">
          <div class="card-head">
            <span class="card-name">{{ t('secpage.vulnTotal') }}</span>
            <el-icon v-if="activeFindings.length" :size="15" class="ic-critical"><WarningFilled /></el-icon>
          </div>
          <div class="card-value">
            <span class="pct" :class="{ critical: activeFindings.length }">{{ activeFindings.length }}</span>
          </div>
          <div class="src-dots">
            <span v-for="n in srcCounts" :key="n.source" class="src-dot-item">
              <span :class="['dot', srcDotClass(n.source)]" />{{ n.source }} {{ n.count }}
            </span>
            <span v-if="!srcCounts.length" class="kpi-sub">{{ t('secpage.noVuln') }}</span>
          </div>
        </el-card>
        <el-card shadow="never" class="metric-card">
          <div class="card-head"><span class="card-name">{{ t('secpage.status') }}</span></div>
          <div class="card-value"><StatusTag :value="snap.status" /></div>
          <div v-if="degradedZones.length" class="zone-list">
            <div v-for="z in degradedZones" :key="z.zone" class="zone-item"
                 :title="z.reason">⚠ {{ z.zone }}：{{ z.reason.slice(0, 40) }}</div>
          </div>
          <div v-else class="kpi-sub">{{ statusZoneHint }}</div>
        </el-card>
        <el-card shadow="never" class="metric-card">
          <div class="card-head"><span class="card-name">{{ t('secpage.scanDate') }}</span></div>
          <div class="card-value"><span class="pct small">{{ snap.scan_date }}</span></div>
          <div class="kpi-sub">{{ t('secpage.lastScan') }}: {{ lastScanAt }}</div>
        </el-card>
        <el-card shadow="never" class="metric-card">
          <div class="card-head"><span class="card-name">{{ t('secpage.baselineChanges') }}</span></div>
          <div class="card-value">
            <span class="pct" :class="{ warn: baselineTotal > 0 }">{{ baselineTotal }}</span>
          </div>
          <div class="kpi-sub">{{ t('secpage.baselineHint') }}</div>
        </el-card>
      </div>
    </el-card>

    <!-- B AI 摘要卡：当日纯文本渲染（{{ }} 转义，禁 v-html——注入面裁定）+ 历史折叠 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.aiSummary') }}</span>
          <el-collapse style="border:none;width:60%" v-if="historySummaries.length">
            <el-collapse-item :title="t('secpage.historySummaries')" name="h">
              <div v-for="h in historySummaries" :key="h.scan_date" class="hist-item">
                <span class="hist-date">{{ h.scan_date }}</span>
                <span class="hist-preview">{{ h.ai_summary_preview || t('secpage.noSummary') }}</span>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </template>
      <div v-if="snap.ai_summary" class="ai-text">{{ snap.ai_summary }}</div>
      <div v-else class="sec-empty">{{ t('secpage.noSummary') }}</div>
    </el-card>

    <!-- C 漏洞列表：停留天数降序（后端已排）；已修复灰显沉底；按来源筛选 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.vulnList') }}</span>
          <el-select v-model="srcFilter" clearable size="small" style="width:140px"
                     :placeholder="t('secpage.filterBySource')">
            <el-option v-for="s in allSources" :key="s" :label="s" :value="s" />
          </el-select>
        </div>
      </template>
      <TableShell :data="filteredFindings" storage-key="security-findings" style="width:100%"
                  :row-class-name="({ row }) => row.fixed ? 'fixed-row' : ''">
        <el-table-column prop="pkg" :label="t('secpage.pkg')" min-width="140" />
        <el-table-column prop="vuln_id" :label="t('secpage.vulnId')" min-width="160" />
        <el-table-column prop="severity" :label="t('secpage.severity')" min-width="95" sortable
                         :sort-method="(a, b) => sevRank(b.severity) - sevRank(a.severity)">
          <template #default="{ row }">
            <span v-if="row.severity" :class="['sev', 'sev-' + row.severity.toLowerCase()]">{{ row.severity }}</span>
            <span v-else class="sev-none">{{ t('secpage.sevUnrated') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="fix_version" :label="t('secpage.fixVersion')" min-width="110">
          <template #default="{ row }">{{ row.fix_version || '—' }}</template>
        </el-table-column>
        <el-table-column prop="expose_days" :label="t('secpage.exposeDays')" min-width="110" sortable>
          <template #default="{ row }">
            <span :class="{ 'dw-old': row.expose_days >= 30 }"
                  :title="row.expose_est ? t('secpage.exposeEstHint') : ''">{{ row.expose_days }}{{ row.expose_est ? '≈' : '' }}</span>
            <span class="dwell-sub">（{{ t('secpage.found') }} {{ row.dwell_days }}）</span>
          </template>
        </el-table-column>
        <el-table-column prop="source" :label="t('secpage.source')" min-width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.source }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="first_seen" :label="t('secpage.firstSeen')" min-width="110" />
      </TableShell>
    </el-card>

    <!-- D 组件版本表：当前版本 + 距上次变更天数 + 层级 tag（批 122 暴露面分层的视觉表达）+ 备注 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header><span>{{ t('secpage.components') }}</span></template>
      <TableShell :data="componentRows" storage-key="security-components" style="width:100%">
        <el-table-column prop="name" :label="t('secpage.component')" min-width="130" />
        <el-table-column prop="version" :label="t('secpage.version')" min-width="220" show-overflow-tooltip />
        <el-table-column prop="last_changed" :label="t('secpage.lastChanged')" min-width="130">
          <template #default="{ row }">
            {{ row.last_changed || '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="note" :label="t('common.remark')" min-width="200">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- E 基线 diff 时间线：头部 30 天迷你条形图（变更数趋势）+ 逐日折叠明细 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header><span>{{ t('secpage.baselineTimeline') }}</span></template>
      <VChart v-if="sparkOption" :option="sparkOption" autoresize style="height: 96px" />
      <el-collapse v-if="baselineDays.length" v-model="expandedDays">
        <el-collapse-item v-for="d in baselineDays" :key="d.scan_date" :name="d.scan_date">
          <template #title>
            <span class="tl-date">{{ d.scan_date }}</span>
            <el-tag size="small" effect="light" style="margin-left: var(--sp-2)">{{ d.count }}</el-tag>
          </template>
          <div v-for="(item, i) in d.items" :key="i" class="tl-item">· {{ item }}</div>
        </el-collapse-item>
      </el-collapse>
      <EmptyState v-else :description="t('secpage.noBaseline')" size="small" />
    </el-card>
  </template>
  <div v-else v-loading="true" class="sec-loading" />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { WarningFilled } from '@element-plus/icons-vue'
import { getSecuritySnapshot, getSecurityHistory } from '../api'
import { cssVar } from '../utils/cssVar'
import StatusTag from './StatusTag.vue'
import TableShell from './TableShell.vue'
import EmptyState from './EmptyState.vue'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])

const { t } = useI18n()
const snap = ref(null)
const history = ref([])
const loadFailed = ref(false)
const isNoSnapshot = ref(false)
const srcFilter = ref('')
const expandedDays = ref([])
let pollTimer = null

const load = async () => {
  try {
    const [s, h] = await Promise.all([getSecuritySnapshot(), getSecurityHistory(30)])
    snap.value = s
    history.value = h.items || []
    loadFailed.value = false
  } catch (e) {
    // 404（当日未扫）≠ 其他失败（网络/500）：前者提示区信息文案，后者错误文案——
    // 空表=假绿红线，两者都不落到假数据展示（步 4 P1-5）
    if (!snap.value) {
      isNoSnapshot.value = e?.code === 'SECURITY_SNAPSHOT_NOT_FOUND' || e?.detail === 'Not Found'
      loadFailed.value = true
    }
  }
}
onMounted(() => { load(); pollTimer = setInterval(load, 300000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

// A 区
const activeFindings = computed(() => (snap.value?.findings || []).filter(f => !f.fixed))
const srcCounts = computed(() => {
  const m = new Map()
  for (const f of activeFindings.value) m.set(f.source, (m.get(f.source) || 0) + 1)
  return [...m.entries()].map(([source, count]) => ({ source, count }))
})
const srcDotClass = s => ({ 'pip-audit': 'warn', 'dnf': 'critical', 'baseline': 'info' }[s] || 'neutral')
const stale = computed(() => {
  if (!snap.value?.created_at) return true
  return Date.now() - new Date(snap.value.created_at).getTime() > 26 * 3600 * 1000
})
const lastScanAt = computed(() => {
  const ts = snap.value?.created_at
  return ts ? new Date(ts).toLocaleString() : '—'
})
// A 区状态卡：ok=一句话；degraded=降级分区逐条（zone+原因——快照既有数据首渲染）
const statusZoneHint = computed(() => t('secpage.zoneOk'))
const degradedZones = computed(() => snap.value?.degraded_zones || [])

// C 区
const allSources = computed(() => [...new Set((snap.value?.findings || []).map(f => f.source))])
const filteredFindings = computed(() => {
  const all = snap.value?.findings || []
  return srcFilter.value ? all.filter(f => f.source === srcFilter.value) : all
})

// D 区：pip 关注包按暴露面分层（公网认证/传输→数据→框架——出事时的关键行优先）；
// 顶层系统组件在前。距上次变更=history.component_changes 反查最近一次出现日
const KEY_PIP = [
  // 公网认证与传输层（攻击面最前——TLS/签名/凭据）
  'pyjwt', 'bcrypt', 'cryptography', 'uvicorn', 'starlette',
  // 数据层
  'psycopg', 'redis',
  // 框架与任务
  'fastapi', 'celery',
  // 策略运行时
  'vnpy',
]
// C 区严重度排序（Critical 最高→Unknown 最低；NULL 最低）
const _SEV_ORDER = ['critical', 'high', 'medium', 'low', 'negligible', 'unknown']
const sevRank = s => _SEV_ORDER.indexOf((s || '').toLowerCase())
const COMPONENT_NOTES = {
  openssl: 'al8 backport 维护（以 dnf --security 为准）',
  pango: 'weasyprint PDF 渲染系统依赖（dnf 层，非 pip 可重建）',
  cairo: 'weasyprint PDF 渲染系统依赖（dnf 层，非 pip 可重建）',
  'wqy-microhei': 'PDF 中文字体（dnf 层）',
}
// D 区「上次变更日期」：台账（components_ledger）直读——批 123 终结 30 天窗口反查
const ledgerMap = computed(() => {
  const m = {}
  for (const r of (snap.value?.components_ledger || [])) m[`${r.source}/${r.name}`] = r.last_changed
  return m
})
const componentRows = computed(() => {
  const comp = snap.value?.components || {}
  const rows = []
  for (const k of ['nginx', 'openssl', 'postgres', 'redis', 'python', 'pango', 'cairo', 'wqy-microhei']) {
    if (comp[k] !== undefined) rows.push({ name: k, version: comp[k], note: COMPONENT_NOTES[k] || '' })
  }
  const pip = comp.pip || {}
  for (const k of KEY_PIP) {
    if (pip[k]) rows.push({ name: k, version: pip[k], note: '' })
  }
  for (const r of rows) {
    r.last_changed = ledgerMap.value[`system/${r.name}`] || ledgerMap.value[`pip/${r.name}`] || ''
  }
  return rows
})

// E 区：逐日基线变更——count 取 history.baseline_changes；当日明细取 snapshot.baseline_diff
// （历史日明细采集侧未落快照，从简只展示计数，与任务文件「点击仅展开详情」从简裁定一致）
const baselineDays = computed(() => {
  const out = []
  for (const d of history.value) {
    const items = []
    if (d.scan_date === snap.value?.scan_date) {
      const bd = snap.value.baseline_diff || {}
      items.push(...(bd.new_ports || []), ...(bd.new_packages || []), ...(bd.cfg_flips || []))
    }
    out.push({ scan_date: d.scan_date, count: d.baseline_changes || 0, items })
  }
  return out.filter(d => d.count > 0)
})
const baselineTotal = computed(() => (snap.value?.baseline_diff
  ? (snap.value.baseline_diff.new_ports?.length || 0)
    + (snap.value.baseline_diff.new_packages?.length || 0)
    + (snap.value.baseline_diff.cfg_flips?.length || 0)
  : 0))

const historySummaries = computed(() =>
  (history.value || []).filter(d => d.scan_date !== snap.value?.scan_date && d.ai_summary_preview))

// E 区头部迷你条形图：30 天逐日基线变更数（含 0 日——空洞=未扫日，非 0 变更）。
// 复刻 SystemMetricsCards sparkline 约定：cssVar 取实值（canvas 不解析 CSS 变量）、
// animation:false（轮询瞬切不滚动）、hidden 轴只留极简刻度。有变更日着 warn 色。
const sparkOption = computed(() => {
  const days = history.value || []
  if (!days.length) return null
  const chrono = [...days].reverse()   // history 新→旧，图要旧→右新
  const data = chrono.map(d => [d.scan_date, d.baseline_changes || 0])
  const max = Math.max(1, ...data.map(p => p[1]))
  return {
    animation: false,
    grid: { left: 2, right: 4, top: 6, bottom: 18 },
    tooltip: {
      trigger: 'axis', formatter: (ps) => {
        const p = ps[0]
        return `${p.name}<br/>${t('secpage.baselineChanges')}: <b>${p.value}</b>`
      },
    },
    xAxis: {
      type: 'category', data: data.map(p => p[0].slice(5)),   // MM-DD
      axisLine: { lineStyle: { color: cssVar('--border-weak') } },
      axisTick: { show: false },
      axisLabel: { color: cssVar('--text-secondary'), fontSize: 10, hideOverlap: true },
    },
    yAxis: { type: 'value', show: false, min: 0, max },
    series: [{
      type: 'bar', data: data.map(([d, v]) => ({ value: v, itemStyle: { color: v > 0 ? cssVar('--warn-fill') : cssVar('--el-fill-color-light') } })),
      barMaxWidth: 10,
    }],
  }
})
</script>

<style scoped>
/* A 区四卡——同 SystemMetricsCards 形态（wd-20 首屏同构；窄屏 2×2 回退同款） */
.card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-3); }
.metric-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-2); }
.card-name { font-size: var(--fs-label); color: var(--text-secondary); }
.card-value { display: flex; align-items: center; min-height: 34px; margin-bottom: var(--sp-2); }
.pct { font-family: var(--font-num); font-size: var(--fs-page); font-weight: 600; font-variant-numeric: tabular-nums; }
.pct.critical { color: var(--critical); }
.pct.warn { color: var(--warn-fill); }
.pct.small { font-size: var(--fs-body); }
.ic-critical { color: var(--critical); }
.kpi-sub { color: var(--text-secondary); font-size: var(--fs-foot); }
@media (max-width: 900px) { .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.src-dots { display: flex; flex-wrap: wrap; gap: var(--sp-2); font-size: var(--fs-foot); color: var(--text-secondary); }
.src-dot-item { display: inline-flex; align-items: center; gap: 4px; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.warn { background: var(--warn-fill); }
.dot.critical { background: var(--critical); }
.dot.info { background: var(--brand-500); }
.dot.neutral { background: var(--flat); }
.stale-warn { color: var(--warn-fill); font-size: var(--fs-foot); }
.ai-text { white-space: pre-wrap; line-height: 1.7; font-size: var(--fs-body); }
.hist-item { display: flex; gap: var(--sp-3); padding: var(--sp-1) 0; font-size: var(--fs-foot); }
.hist-date { color: var(--text-secondary); flex: none; }
.hist-preview { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-old { color: var(--critical); font-weight: 600; }
.dwell-sub { color: var(--text-secondary); font-size: var(--fs-foot); }
.zone-list { margin-top: var(--sp-1); }
.zone-item { color: var(--warn-fill); font-size: var(--fs-foot); line-height: 1.7;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: default; }
/* 严重度色（tokens 语义复用：critical 深红/warn 橙/主文灰） */
.sev { font-size: var(--fs-foot); font-weight: 600; }
.sev-critical { color: var(--critical); }
.sev-high { color: var(--warn-fill); }
.sev-medium { color: var(--brand-500); }
.sev-low, .sev-negligible, .sev-unknown { color: var(--text-secondary); }
.sev-none { color: var(--flat); font-size: var(--fs-foot); }
.tl-date { font-weight: 600; }
.tl-item { padding: 0 var(--sp-3); color: var(--text-secondary); font-size: var(--fs-foot); line-height: 1.8; }
.sec-empty { color: var(--text-secondary); font-size: var(--fs-foot); padding: var(--sp-3) 0; }
.sec-loading { min-height: 200px; }
:deep(.fixed-row) td { opacity: 0.55; }   /* 已修复项灰显沉底（排序后端已沉底） */
</style>
