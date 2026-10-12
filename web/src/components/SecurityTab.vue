<template>
  <!-- 批 120 线③：安全态势 Tab（五区布局 A~E，任务文件钉死）。
       数据两请求：snapshot（默认今日）+ history（30 天）；轮询 5min（每日数据，无需 ws）。 -->
  <div v-if="loadFailed" class="sec-empty">
    <el-alert :type="isNoSnapshot ? 'info' : 'error'" :closable="false">
      {{ isNoSnapshot ? t('secpage.noSnapshot') : t('secpage.loadFailed') }}
    </el-alert>
  </div>
  <template v-else-if="snap">
    <!-- A 顶部状态卡：漏洞总数+按来源分色点 / status(degraded 黄) / 上次扫描 / 新鲜度(>26h 黄) -->
    <el-card>
      <el-row :gutter="12">
        <el-col :span="6">
          <div class="kpi-label">{{ t('secpage.vulnTotal') }}</div>
          <div class="kpi-num">{{ activeFindings.length }}</div>
          <div class="src-dots">
            <span v-for="n in srcCounts" :key="n.source" class="src-dot-item">
              <span :class="['dot', srcDotClass(n.source)]" />{{ n.source }} {{ n.count }}
            </span>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-label">{{ t('secpage.status') }}</div>
          <StatusTag :value="snap.status" />
          <div v-if="stale" class="stale-warn">{{ t('secpage.stale') }}</div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-label">{{ t('secpage.scanDate') }}</div>
          <div class="kpi-num small">{{ snap.scan_date }}</div>
          <div class="kpi-sub">{{ t('secpage.lastScan') }}: {{ lastScanAt }}</div>
        </el-col>
        <el-col :span="6">
          <div class="kpi-label">{{ t('secpage.baselineChanges') }}</div>
          <div class="kpi-num">{{ baselineTotal }}</div>
          <div class="kpi-sub">{{ t('secpage.baselineHint') }}</div>
        </el-col>
      </el-row>
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
        <el-table-column prop="fix_version" :label="t('secpage.fixVersion')" min-width="110">
          <template #default="{ row }">{{ row.fix_version || '—' }}</template>
        </el-table-column>
        <el-table-column prop="dwell_days" :label="t('secpage.dwellDays')" min-width="100" sortable>
          <template #default="{ row }">
            <span :class="{ 'dw-old': row.dwell_days >= 30 }">{{ row.dwell_days }}</span>
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

    <!-- D 组件版本表：当前版本 + 距上次变更天数（history.component_changes 反查）+ 备注 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header><span>{{ t('secpage.components') }}</span></template>
      <TableShell :data="componentRows" storage-key="security-components" style="width:100%">
        <el-table-column prop="name" :label="t('secpage.component')" min-width="130" />
        <el-table-column prop="version" :label="t('secpage.version')" min-width="220" show-overflow-tooltip />
        <el-table-column prop="since_days" :label="t('secpage.daysSinceChange')" min-width="130">
          <template #default="{ row }">
            {{ row.since_days === null ? '—' : row.since_days }}
          </template>
        </el-table-column>
        <el-table-column prop="note" :label="t('common.remark')" min-width="200">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- E 基线 diff 时间线：近 30 天逐日变更（计数来自 history；当日明细来自 snapshot 展开） -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header><span>{{ t('secpage.baselineTimeline') }}</span></template>
      <el-collapse v-if="baselineDays.length" v-model="expandedDays">
        <el-collapse-item v-for="d in baselineDays" :key="d.scan_date" :name="d.scan_date">
          <template #title>
            <span class="tl-date">{{ d.scan_date }}</span>
            <el-tag size="small" effect="light" style="margin-left: var(--sp-2)">{{ d.count }}</el-tag>
          </template>
          <div v-for="(item, i) in d.items" :key="i" class="tl-item">· {{ item }}</div>
        </el-collapse-item>
      </el-collapse>
      <div v-else class="sec-empty">{{ t('secpage.noBaseline') }}</div>
    </el-card>
  </template>
  <div v-else v-loading="true" class="sec-loading" />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
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

// C 区
const allSources = computed(() => [...new Set((snap.value?.findings || []).map(f => f.source))])
const filteredFindings = computed(() => {
  const all = snap.value?.findings || []
  return srcFilter.value ? all.filter(f => f.source === srcFilter.value) : all
})

// D 区：pip 关键包 + 顶层组件；距上次变更=history.component_changes 反查最近一次出现日
const KEY_PIP = ['fastapi', 'vnpy', 'psycopg', 'pandas', 'numpy', 'celery', 'redis']
const COMPONENT_NOTES = {
  openssl: 'al8 backport 维护（以 dnf --security 为准）',
  pango: 'weasyprint PDF 渲染系统依赖（dnf 层，非 pip 可重建）',
  cairo: 'weasyprint PDF 渲染系统依赖（dnf 层，非 pip 可重建）',
  'wqy-microhei': 'PDF 中文字体（dnf 层）',
}
const lastChangeDate = (name) => {
  for (const d of history.value) {
    if ((d.component_changes || []).includes(name)) return d.scan_date
  }
  return null
}
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
  const today = snap.value?.scan_date
  for (const r of rows) {
    const lc = lastChangeDate(r.name)
    r.since_days = (lc && today) ? Math.max(0, Math.round((new Date(today) - new Date(lc)) / 86400000)) : null
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
</script>

<style scoped>
.kpi-label { color: var(--text-secondary); font-size: var(--fs-label); }
.kpi-num { font-size: var(--fs-kpi); margin-top: var(--sp-1); }
.kpi-num.small { font-size: var(--fs-body); font-weight: 600; }
.kpi-sub { color: var(--text-secondary); font-size: var(--fs-foot); margin-top: var(--sp-1); }
.src-dots { display: flex; flex-wrap: wrap; gap: var(--sp-2); margin-top: var(--sp-1); font-size: var(--fs-foot); color: var(--text-secondary); }
.src-dot-item { display: inline-flex; align-items: center; gap: 4px; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot.warn { background: var(--warn-fill); }
.dot.critical { background: var(--critical); }
.dot.info { background: var(--brand-500); }
.dot.neutral { background: var(--flat); }
.stale-warn { color: var(--warn-fill); font-size: var(--fs-foot); margin-top: var(--sp-1); }
.ai-text { white-space: pre-wrap; line-height: 1.7; font-size: var(--fs-body); }
.hist-item { display: flex; gap: var(--sp-3); padding: var(--sp-1) 0; font-size: var(--fs-foot); }
.hist-date { color: var(--text-secondary); flex: none; }
.hist-preview { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-old { color: var(--critical); font-weight: 600; }
.tl-date { font-weight: 600; }
.tl-item { padding: 0 var(--sp-3); color: var(--text-secondary); font-size: var(--fs-foot); line-height: 1.8; }
.sec-empty { color: var(--text-secondary); font-size: var(--fs-foot); padding: var(--sp-3) 0; }
.sec-loading { min-height: 200px; }
:deep(.fixed-row) td { opacity: 0.55; }   /* 已修复项灰显沉底（排序后端已沉底） */
</style>
