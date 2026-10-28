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
          <div style="display:flex;align-items:center;gap:12px">
            <span v-if="stale" class="stale-warn">{{ t('secpage.stale') }}</span>
            <!-- 批 138/141：手动重扫——按全站规范用 IconBtn（纯图标+title 提示，批 21 立法） -->
            <IconBtn :icon="Refresh" :title="t('secpage.rescan')" :loading="scanning || localPending"
                     :disabled="scanning || localPending" @click="onRescan" />
          </div>
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
            <span v-if="!srcCounts.length" class="kpi-sub">{{ t('secpage.noVulnOpt') }}</span>
          </div>
        </el-card>
        <el-card shadow="never" class="metric-card">
          <div class="card-head"><span class="card-name">{{ t('secpage.status') }}</span></div>
          <div class="card-value"><StatusTag :value="snap.status" /></div>
          <div v-if="degradedZones.length" class="zone-list">
            <div v-for="z in degradedZones" :key="z.zone" class="zone-item"
                 :title="z.reason">⚠ {{ z.zone }}：{{ z.reason.slice(0, 40) }}</div>
          </div>
          <div class="kpi-sub">{{ t('secpage.lastScan') }}: {{ lastScanAt }}</div>
        </el-card>
        <el-card shadow="never" class="metric-card">
          <div class="card-head"><span class="card-name">{{ t('secpage.scanDate') }}</span></div>
          <div class="card-value"><span class="pct small">{{ snap.scan_date }}</span></div>
          <div class="kpi-sub dep-outdated" :class="{ warn: depOutdatedWarn }">
            {{ t('secpage.depOutdated') }}: {{ snap.dep_health?.has_update_n ?? '—' }}
          </div>
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
          <!-- 批 140：历史摘要折叠区 → 图标按钮；批 154 统一 IconBtn（全站规范——纯图标+title，与重扫钮同构） -->
          <IconBtn :icon="Tickets" :title="t('secpage.historySummaries')"
                   :disabled="!historySummaries.length" @click="histDialog = true" />
        </div>
      </template>
      <div v-if="snap.ai_summary" class="ai-text">{{ snap.ai_summary }}</div>
      <div v-else class="sec-empty">{{ t('secpage.noSummary') }}</div>
    </el-card>

    <!-- C 漏洞列表（批 125 四项用户裁定）：
         ① ID 第一列+严重度第二列（ID=引用主键）；② vuln_id 超链到权威页
         （CVE→NVD / GHSA→GitHub advisories——PYSEC 无稳定单页不带链）；
         ③ 状态列（未修复/已修复）——行筛选用（原 header checkbox 裁）；
         ④ description 列（show-overflow-tooltip 一行截断，完整文本 hover）。 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.vulnList') }}</span>
          <div style="display:flex;align-items:center;gap:var(--icon-gap)">
            <!-- 批126d：搜索在左/图标钮在右（输入在左动作在右惯例）；两表统一 200px（用户裁定） -->
            <el-input v-model="vulnSearch" clearable size="small" style="width:200px"
                      :placeholder="t('secpage.searchVuln')" />
            <RowFilter v-model="vulnRowFilter" :groups="vulnFilterGroups"
                       :title="t('common.filter')" />
          </div>
        </div>
      </template>
      <TableShell :data="filteredFindings" storage-key="security-findings" style="width:100%">
        <el-table-column prop="vuln_id" :label="t('secpage.vulnId')" min-width="170">
          <template #default="{ row }">
            <a v-if="vulnUrl(row.vuln_id)" :href="vulnUrl(row.vuln_id)" target="_blank"
               rel="noopener noreferrer" class="vuln-link">{{ row.vuln_id }}</a>
            <span v-else>{{ row.vuln_id }}</span>
          </template>
        </el-table-column>
        <!-- 批126i：描述列移第二列（用户裁定——编号后即看本质，严重级别等量化列随右） -->
        <el-table-column v-if="hasDesc" prop="description" :label="t('secpage.descCol')"
                         min-width="260" show-overflow-tooltip>
          <template #default="{ row }">{{ descOf(row) }}</template>
        </el-table-column>
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
        <el-table-column prop="fixed" :label="t('secpage.vulnState')" min-width="90">
          <template #default="{ row }">
            <span :class="row.fixed ? 'sev-none' : 'sev-critical'">
              {{ row.fixed ? t('secpage.fixedLabel') : t('secpage.activeLabel') }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('secpage.published')" min-width="110">
          <template #default="{ row }">
            <span>{{ row.published || row.first_seen }}</span>
            <span v-if="!row.published" class="pub-est">≈</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.action')" min-width="70">
          <template #default="{ row }">
            <IconBtn v-if="row.fixed" :icon="Delete" type="danger" size="small"
                     :title="t('common.delete')" @click="onDeleteFinding(row)" />
          </template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- D 组件列表（批 124：版本表→列表，全量组件 + 来源自解释 + 漏洞数 + RowFilter）。
         行筛选走仓内既有体系（RowFilter 分组多选——同 Audit/Logs 范式）：来源类型
         （数据 distinct）× 漏洞状态（默认勾"有漏洞"=行动导向视图，状态可见可改——
         取消即健康面）；搜索=name/origin/version 三字段子串（叠加上述过滤）。
         来源列=采集侧从 SBOM locations + 依赖链自动推导（「ffmpeg 哪来的」在表里）。 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>{{ t('secpage.components') }}
            <span class="kpi-sub" style="margin-left: var(--sp-2)">{{ compHint }}</span>
          </span>
          <div style="display:flex;align-items:center;gap:var(--icon-gap)">
            <!-- 批126d：搜索在左/图标钮在右+统一 200px（与 C 区同款） -->
            <el-input v-model="compFilter" clearable size="small" style="width:200px"
                      :placeholder="t('secpage.searchComponent')" />
            <RowFilter v-model="compRowFilter" :groups="compFilterGroups"
                       :title="t('common.filter')" />
          </div>
        </div>
      </template>
      <TableShell :data="compRowsShown" storage-key="security-components" style="width:100%">
        <el-table-column prop="name" :label="t('secpage.component')" min-width="170" />
        <el-table-column prop="origin" :label="t('secpage.origin')" min-width="150" show-overflow-tooltip />
        <el-table-column prop="last_changed" :label="t('secpage.lastChanged')" min-width="120">
          <template #default="{ row }">{{ row.last_changed || '—' }}</template>
        </el-table-column>
        <el-table-column prop="version" :label="t('secpage.version')" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.version }}</template>
        </el-table-column>
        <el-table-column prop="vuln_count" :label="t('secpage.vulnCountCol')" min-width="90"
                         :sort-method="(a, b) => b.vuln_count - a.vuln_count" sortable>
          <template #default="{ row }">
            <span v-if="row.vuln_count" class="sev-critical">{{ row.vuln_count }}</span>
            <span v-else class="sev-none">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="latest_version" :label="t('secpage.latestCol')" min-width="130">
          <template #default="{ row }">
            <span :title="row.judge_why">{{ row.latest_version || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="has_update" :label="t('secpage.hasUpdate')" min-width="110">
          <template #default="{ row }">
            <span :title="row.judge_why">{{ hasUpdateText(row.has_update) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="stale_days" :label="t('secpage.staleDays')" min-width="100">
          <template #default="{ row }">{{ row.stale_days ?? '—' }}</template>
        </el-table-column>
      </TableShell>
    </el-card>

    <!-- 批 124：原 E 区「基线变更时间线」删除（用户裁定无价值：冷启动无数据、
         历史只存计数不存明细——基线变化本身已在 A 区卡与告警面覆盖） -->

    <!-- 批 140：AI 历史摘要弹窗（日期/摘要/操作——用户裁定折叠区改按钮） -->
    <el-dialog v-model="histDialog" :title="t('secpage.historySummaries')" width="760px">
      <el-table :data="historySummaries" size="small" max-height="480">
        <el-table-column prop="scan_date" :label="t('secpage.histDate')" width="110" />
        <el-table-column :label="t('secpage.aiSummary')" min-width="420">
          <template #default="{ row }">
            <span style="white-space:pre-wrap">{{ row.ai_summary || t('secpage.noSummary') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('secpage.histOps')" width="80" align="center">
          <template #default="{ row }">
            <IconBtn :icon="Delete" type="danger" size="small"
                     :title="t('common.delete')" @click="onDeleteSummary(row.scan_date)" />
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </template>
  <div v-else v-loading="true" class="sec-loading" />
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { WarningFilled, Delete, Refresh, Tickets } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSecuritySnapshot, getSecurityHistory, deleteSecurityFinding, triggerSecurityScan, deleteSecuritySummary } from '../api'
import StatusTag from './StatusTag.vue'
import TableShell from './TableShell.vue'
import RowFilter from './RowFilter.vue'
import IconBtn from './IconBtn.vue'


const { t, locale } = useI18n()
const snap = ref(null)
const history = ref([])
const loadFailed = ref(false)
const isNoSnapshot = ref(false)
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

// 批 138：手动重扫——入队后轮询 scan_date 变化（采集分钟级，上限 5 分钟），完成即刷新并提示
// 批 155 §4.4c：扫描态=服务端真源 scan_running（刷新/跨端一致）；本地布尔仅防抖
const scanning = computed(() => !!(snap.value?.scan_running))
const localPending = ref(false)
const onRescan = async () => {
  if (scanning.value || localPending.value) return
  localPending.value = true
  try {
    await triggerSecurityScan()
    ElMessage.success(t('secpage.rescanStarted'))
    const before = snap.value?.scan_date
    for (let i = 0; i < 90; i++) {
      await new Promise(r => setTimeout(r, 10000))
      await load()
      if (!snap.value?.scan_running && snap.value?.scan_date) break
      if (snap.value?.scan_date !== before) { }   // 日期变但锁未放=继续等锁
    }
    ElMessage.success(t('secpage.rescanDone'))
    localPending.value = false          // 轮询确认完成才解禁（全程禁用无缝隙）
  } catch (e) {
    if (e?.code === 'SEC_SCAN_RUNNING') ElMessage.warning(t('secpage.scanRunning'))
    else ElMessage.error(e?.detail || t('common.deleteFailed'))
    localPending.value = false          // 仅失败/409 解禁；成功=轮询确认完成才解禁（全周期禁用）
  }
}

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
// 批 154 补③：依赖滞后（A 区小字+D 区箭头列——检查不升级；>20 琥珀告警）
// 键归一匹配：pip 名小写下划线/连字符混形 vs ledger 名（PyYAML/pyyaml、typing_extensions）
// 批 154 补5：依赖滞后（A 区小字；D 区三态列直读 row.latest，服务端单源）
const depOutdatedWarn = computed(() => (snap.value?.dep_health?.has_update_n || 0) > 20)
const hasUpdateText = (v) => v === 'yes' ? t('secpage.huYes') : v === 'no' ? t('secpage.huNo') : t('secpage.huUnknown')
// A 区状态卡：ok=一句话；degraded=降级分区逐条（zone+原因——快照既有数据首渲染）
const degradedZones = computed(() => snap.value?.degraded_zones || [])

// C 区（批 125 重构）：搜索（ID/包/描述子串）× RowFilter（严重度 distinct × 状态
// 默认 active——原 header checkbox 裁，状态语义归筛选组；红点/清除钮组件级生效）
const vulnSearch = ref('')
const vulnRowFilter = ref({ fixed: ['active'] })
const vulnFilterGroups = computed(() => {
  const sevs = [...new Set((snap.value?.findings || []).map(f => f.severity).filter(Boolean))]
  sevs.sort((a, b) => _SEV_ORDER.indexOf(a.toLowerCase()) - _SEV_ORDER.indexOf(b.toLowerCase()))
  return [
    { key: 'severity', label: t('secpage.severity'),
      options: sevs.map(s => ({ value: s, label: s })) },
    { key: 'fixed', label: t('secpage.vulnState'), options: [
      { value: 'active', label: t('secpage.activeLabel') },
      { value: 'fixed', label: t('secpage.fixedLabel') },
    ] },
  ]
})
const filteredFindings = computed(() => {
  const all = snap.value?.findings || []
  const rf = vulnRowFilter.value
  const sev = rf.severity ?? []
  // 批126c 修：原 rf.fixed ?? ['active'] 与「空=全显」契约冲突——清除筛选后 fallback
  // 又把 active 塞回=列表不变（用户实测 bug）。默认行动导向视图由上方初值
  // {fixed:['active']} 承载；清除⇒{}⇒此处 []=全显，与 D 区 v2 同法
  const fx = rf.fixed ?? []
  const q = vulnSearch.value.trim().toLowerCase()
  return all.filter(f =>
    (!sev.length || sev.includes(f.severity)) &&
    (!fx.length || (fx.includes('active') && !f.fixed) || (fx.includes('fixed') && f.fixed)))
    .filter(f => !q || `${f.vuln_id} ${f.pkg} ${f.description || ''} ${f.description_en || ''}`
      .toLowerCase().includes(q))
})
// 描述列：双语按 locale 取（批 126——空描述显示 '—'，backfill 补齐后自动切换）
const descOf = row => (locale.value === 'zh' ? row.description : row.description_en)
  || row.description || row.description_en || '—'
// 描述列只在有数据时渲染（表列不空占）
const hasDesc = computed(() => (snap.value?.findings || []).some(f => f.description || f.description_en))
// vuln_id → 权威页（CVE→NVD；GHSA→GitHub；PYSEC/PYSEC 无稳定单页不带链）
const vulnUrl = (id) => {
  if (/^CVE-/i.test(id)) return `https://nvd.nist.gov/vuln/detail/${id}`
  if (/^GHSA-/i.test(id)) return `https://github.com/advisories/${id}`
  return null
}

// C 区严重度排序（Critical 最高→Unknown 最低；NULL 最低）
const _SEV_ORDER = ['critical', 'high', 'medium', 'low', 'negligible', 'unknown']
const sevRank = s => _SEV_ORDER.indexOf((s || '').toLowerCase())
const compFilter = ref('')
// 行筛选（RowFilter 分组——批 124）：默认 {vuln: ['yes']}（=行动导向默认视图，
// 状态可见可改——取消"有漏洞"=健康面）；来源组选项从台账数据 distinct 预取
const compRowFilter = ref({ vuln: ['yes'] })   // 初始=行动导向；「全部」清 {} ⇒ 下行 hasOwn 判据区分
// 组件全量（台账：pip+rpm+binary+system——批 124 起 binary 也入账，ffmpeg 这类
// 捆绑原生库由此可见）+ 每组件漏洞数（findings 聚合）
const componentRows = computed(() => {
  const led = snap.value?.components_ledger || []
  // 漏洞数=**active** 聚合（批 125 修：与 A/C 区同口径——此前含 fixed 导致
  // 「漏洞表空、组件表还有漏洞」的不一致；已修复历史不进组件当前风险计数）
  const vc = {}
  for (const f of (snap.value?.findings || [])) {
    if (!f.fixed) vc[f.pkg] = (vc[f.pkg] || 0) + 1
  }
  return led.map(r => ({ ...r, vuln_count: vc[r.name] || 0 }))
})
// 行筛选组（Audit 范式）：来源类型（distinct 预取）× 漏洞状态（固定枚举）
const compFilterGroups = computed(() => {
  const srcs = [...new Set(componentRows.value.map(r => r.source))].sort()
  return [
    { key: 'source', label: t('secpage.originCol'), options: srcs.map(s => ({ value: s, label: s })) },
    { key: 'vuln', label: t('secpage.vulnState'), options: [
      { value: 'yes', label: t('secpage.hasVuln') },
      { value: 'no', label: t('secpage.noVulnOpt') },
    ] },
    // 批 155：是否有更新版本（多选标签惯例；「未知」不命中任何标签）
    { key: 'upd', label: t('secpage.hasUpdate'), options: [
      { value: 'yes', label: t('secpage.huYes') },
      { value: 'no', label: t('secpage.huNo') },
    ] },
  ]
})
// 三层叠加：RowFilter（来源×状态）→ 搜索（name/origin/version 多字段子串——
// "vnpy" 能带出 origin=vnpy→pyside6 捆绑 的 ffmpeg）
const compRowsShown = computed(() => {
  const all = componentRows.value
  const rf = compRowFilter.value
  // 「全部」语义修复 v2（批 125）：永远按 rf.vuln 实际值——初始 ['yes']（行动导向）、
  // 点「全部」onAll 清成 {} 后无 key ⇒ **默认 []（全部显示）**。v1 的 hasOwn 判据
  // 写反（无 key 给 ['yes'] = 全选后回到只看有漏洞=「没反应」的根因）。
  const vuln = rf.vuln ?? []
  const source = rf.source ?? []
  const upd = rf.upd ?? []
  const rows = all.filter(r =>
    (!source.length || source.includes(r.source)) &&
    (!vuln.length || (vuln.includes('yes') && r.vuln_count) || (vuln.includes('no') && !r.vuln_count)) &&
    (!upd.length || upd.includes(r.has_update)))   // 未知(undefined)不命中任何标签✓
  if (!compFilter.value.trim()) return rows
  const q = compFilter.value.toLowerCase()
  return rows.filter(r =>
    `${r.name || ''} ${r.origin || ''} ${r.version || ''}`.toLowerCase().includes(q))
})
const compHint = computed(() =>
  t('secpage.compMatch', { n: compRowsShown.value.length, total: componentRows.value.length }))

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

// 已修复行显式删除（批 126 用户裁定：弃隐藏 GC，行内操作可见可审——后端守卫
// last_seen<今日+admin 权限+审计；前端确认弹窗=仓内范式 BacktestRun.vue:152）
const onDeleteFinding = async (row) => {
  try {
    await ElMessageBox.confirm(
      t('secpage.confirmDelFinding', { id: row.vuln_id, pkg: row.pkg }),
      t('common.confirm'), { type: 'warning' })
  } catch { return }   // 取消=不动
  try {
    await deleteSecurityFinding(row.pkg, row.vuln_id)
    ElMessage.success(t('common.deleteSuccess'))
    load()
  } catch (e) {
    ElMessage.error(e?.detail || t('common.deleteFailed'))
  }
}

const historySummaries = computed(() =>
  (history.value || []).filter(d => d.scan_date !== snap.value?.scan_date && d.ai_summary_preview))

// 批 140：历史摘要弹窗——逐条删除（删整日快照行；确认后删并原位刷新）
const histDialog = ref(false)
const onDeleteSummary = async (d) => {
  try {
    await ElMessageBox.confirm(t('secpage.histDelConfirm', { date: d }),
                              t('secpage.historySummaries'), { type: 'warning' })
  } catch { return }   // 取消=不动
  try {
    await deleteSecuritySummary(d.replaceAll('-', ''))
    const [s, h] = await Promise.all([getSecuritySnapshot(), getSecurityHistory(30)])
    snap.value = s
    history.value = h.items || []
    ElMessage.success(t('common.deleteSuccess'))
  } catch (e) {
    ElMessage.error(e?.detail || t('common.deleteFailed'))
  }
}

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
.dot { width: var(--dot); height: var(--dot); border-radius: 50%; display: inline-block; }
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
.vuln-link { color: var(--brand-500); text-decoration: none; }
.vuln-link:hover { text-decoration: underline; }
.dwell-sub { color: var(--text-secondary); font-size: var(--fs-foot); }
.zone-list { margin-top: var(--sp-1); }
.zone-item { color: var(--warn-fill); font-size: var(--fs-foot); line-height: 1.7;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: default; }
/* 严重度色（tokens 语义复用：critical 深红/warn 橙/主文灰） */
.sev { font-size: var(--fs-foot); font-weight: 600; }
.sev-critical { color: var(--critical); }
/* 批 154 补：非最新版橙黄（当前版本+最新版列同色——滞后即提示） */
/* 批 155 用户裁定：取消依赖颜色标示（分类靠是/否筛选列）——ver-stale/latest-new/ver-uptodate 全撤 */
.sev-high { color: var(--warn-fill); }
.sev-medium { color: var(--brand-500); }
.sev-low, .sev-negligible, .sev-unknown { color: var(--text-secondary); }
.sev-none { color: var(--flat); font-size: var(--fs-foot); }
.tl-date { font-weight: 600; }
.tl-item { padding: 0 var(--sp-3); color: var(--text-secondary); font-size: var(--fs-foot); line-height: 1.8; }
.sec-empty { color: var(--text-secondary); font-size: var(--fs-foot); padding: var(--sp-3) 0; }
.sec-loading { min-height: 200px; }
</style>
