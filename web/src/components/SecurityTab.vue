<template>
  <!-- 系统安全 Tab（批 120 建，批 122/123/124 演进）：四区布局 A~D——
       A 状态卡 / B AI 摘要 / C 漏洞列表 / D 组件列表（原 E 区基线时间线批 124 删）。
       数据两请求：snapshot（默认今日）+ history（30 天，B 区历史摘要用）；轮询 5min。
       批 124 体验：C 区严重度前置+去单值来源列；D 区全量组件+来源自解释（origin
       自动推导）+漏洞数+默认有漏洞视图+搜索；A 区四卡化（首屏同构 wd-20）。 -->
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
          <div class="card-head"><span class="card-name">{{ t('secpage.vulnDb') }}</span></div>
          <div class="card-value"><span class="pct small">{{ dbBuiltDate || '—' }}</span></div>
          <div class="kpi-sub">{{ dbStateHint }}</div>
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

    <!-- C 漏洞列表：**漏洞 ID 第一列、严重度第二列**（用户裁定 2026-10-15——ID 是引用/检索
         主键，先见标识再判严重度）；来源列删（单值时零信息量——筛选器多源才显示）；
         后端已按 严重度→暴露时长 排序；已修复灰显沉底 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.vulnList') }}</span>
          <el-select v-if="allSources.length > 1" v-model="srcFilter" clearable size="small" style="width:140px"
                     :placeholder="t('secpage.filterBySource')">
            <el-option v-for="s in allSources" :key="s" :label="s" :value="s" />
          </el-select>
        </div>
      </template>
      <TableShell :data="filteredFindings" storage-key="security-findings" style="width:100%"
                  :row-class-name="({ row }) => row.fixed ? 'fixed-row' : ''">
        <el-table-column prop="vuln_id" :label="t('secpage.vulnId')" min-width="170" />
        <el-table-column prop="severity" :label="t('secpage.severity')" min-width="95" sortable
                         :sort-method="(a, b) => sevRank(b.severity) - sevRank(a.severity)">
          <template #default="{ row }">
            <span v-if="row.severity" :class="['sev', 'sev-' + row.severity.toLowerCase()]">{{ row.severity }}</span>
            <span v-else class="sev-none">{{ t('secpage.sevUnrated') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pkg" :label="t('secpage.pkg')" min-width="140" />
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
        <el-table-column prop="first_seen" :label="t('secpage.firstSeen')" min-width="110" />
      </TableShell>
    </el-card>

    <!-- D 组件列表（批 124：版本表→列表，全量组件 + 来源自解释 + 漏洞数）。
         默认视图=有漏洞的（行动导向）；搜索时查全量（信息完备）——业界组件清单惯例：
         默认聚焦可行动项、搜索兜底完备性。来源列=采集侧从 SBOM locations 自动推导
         （origin，无需人工备注——「ffmpeg 哪来的」答案在表里）。 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.components') }}
            <span class="kpi-sub" style="margin-left: var(--sp-2)">{{ compHint }}</span>
          </span>
          <el-input v-model="compFilter" clearable size="small" style="width:200px"
                    :placeholder="t('secpage.searchComponent')" />
        </div>
      </template>
      <TableShell :data="compRowsShown" storage-key="security-components" style="width:100%">
        <el-table-column prop="name" :label="t('secpage.component')" min-width="170" />
        <el-table-column prop="version" :label="t('secpage.version')" min-width="180" show-overflow-tooltip />
        <el-table-column prop="origin" :label="t('secpage.origin')" min-width="150" show-overflow-tooltip />
        <el-table-column prop="vuln_count" :label="t('secpage.vulnCount')" min-width="90"
                         :sort-method="(a, b) => b.vuln_count - a.vuln_count" sortable>
          <template #default="{ row }">
            <span v-if="row.vuln_count" class="sev-critical">{{ row.vuln_count }}</span>
            <span v-else class="sev-none">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="last_changed" :label="t('secpage.lastChanged')" min-width="120">
          <template #default="{ row }">{{ row.last_changed || '—' }}</template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- 批 124：原 E 区「基线变更时间线」删除（用户裁定无价值：冷启动无数据、
         历史只存计数不存明细——基线变化本身已在 A 区卡与告警面覆盖） -->
  </template>
  <div v-else v-loading="true" class="sec-loading" />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { WarningFilled } from '@element-plus/icons-vue'
import { getSecuritySnapshot, getSecurityHistory } from '../api'
import StatusTag from './StatusTag.vue'
import TableShell from './TableShell.vue'


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

// C 区严重度排序（Critical 最高→Unknown 最低；NULL 最低）
const _SEV_ORDER = ['critical', 'high', 'medium', 'low', 'negligible', 'unknown']
const sevRank = s => _SEV_ORDER.indexOf((s || '').toLowerCase())
const compFilter = ref('')
// 组件全量（台账：pip+rpm+binary+system——批 124 起 binary 也入账，ffmpeg 这类
// 捆绑原生库由此可见）+ 每组件漏洞数（findings 聚合）
const componentRows = computed(() => {
  const led = snap.value?.components_ledger || []
  const vc = {}
  for (const f of (snap.value?.findings || [])) vc[f.pkg] = (vc[f.pkg] || 0) + 1
  return led.map(r => ({ ...r, vuln_count: vc[r.name] || 0 }))
})
// 默认=有漏洞的（行动导向）；输入搜索=全量匹配（信息完备）
const compRowsShown = computed(() => {
  const all = componentRows.value
  if (compFilter.value) {
    const q = compFilter.value.toLowerCase()
    return all.filter(r => (r.name || '').toLowerCase().includes(q))
  }
  return all.filter(r => r.vuln_count > 0)
})
const compHint = computed(() => compFilter.value
  ? t('secpage.compMatch', { n: compRowsShown.value.length, total: componentRows.value.length })
  : t('secpage.compVulnOnly', { n: compRowsShown.value.length, total: componentRows.value.length }))

// A 区漏洞库卡（批 124 用户裁定换基线偏差卡——「漏洞判定的时效边界」）：
// DB 构建日（import.json URL 时间戳——采集器提取）；>3 天黄（上游日更 1 次，
// 超龄=anchore 断更或 update 通道故障——值得人看一眼）
const dbBuiltDate = computed(() => {
  const s = (snap.value?.vulns_db || {}).built_at
  return s ? s.slice(0, 10) : ''
})
const dbStateHint = computed(() => {
  const vd = snap.value?.vulns_db || {}
  const days = vd.built_at
    ? Math.floor((Date.now() - new Date(vd.built_at).getTime()) / 86400000) : null
  if (vd.state === 'update-failed') return t('secpage.dbUpdateFailed')
  if (days === null) return t('secpage.dbUnknown')
  return days > 3 ? t('secpage.dbStale', { n: days }) : t('secpage.dbFresh', { n: days })
})

const historySummaries = computed(() =>
  (history.value || []).filter(d => d.scan_date !== snap.value?.scan_date && d.ai_summary_preview))

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
