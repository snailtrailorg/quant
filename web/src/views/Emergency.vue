<template>
  <div>
    <!-- 批 171q：紧急处置页（用户终版——横排四卡操作带；范围=系统账号真源；无状态区/无页脚说明） -->
    <!-- 1 处置范围（数据源=trading_account 全量行；未启用灰态，禁自造通道） -->
    <el-card>
      <template #header>{{ t('emg.scope') }}</template>
      <div class="scope-list">
        <el-checkbox v-for="a in accounts" :key="a.account_id" v-model="a.checked" :disabled="!a.enabled">
          <span>{{ a.name }}</span>
          <span class="kpi-sub scope-meta">
            {{ t('emg.working') }}: {{ a.working_orders == null ? '—' : (a.working_orders < 0 ? t('emg.unknown') : a.working_orders) }}
            · {{ t('emg.posValue') }}: {{ a.position_value == null ? t('emg.unknown') : '¥' + Number(a.position_value).toLocaleString() }}
          </span>
          <span v-if="(a.working_orders || 0) > 0" class="dot-live" :title="t('emg.hasWorking')" />
          <span v-if="!a.enabled" class="kpi-sub">（{{ t('emg.disabledAccount') }}）</span>
          <span v-else-if="!a.running" class="kpi-sub">（{{ t('emg.notRunning') }}）</span>
        </el-checkbox>
      </div>
    </el-card>

    <!-- 2 处置操作（横排四卡；递进：撤单 ⊂ 熔断 ⊂ 清仓；恢复仅解除拦截） -->
    <div class="op-grid">
      <el-card v-for="(op, i) in ops" :key="op.key" class="op-card">
        <div class="op-name">{{ i + 1 }}. {{ t(op.name) }}</div>
        <div class="op-sub">{{ t(op.desc) }}</div>
        <el-button :type="op.btnType" :plain="op.plain" :disabled="op.disabled()"
                   class="op-btn" @click="op.run">
          {{ t(op.btn) }}<template v-if="op.withCount">（{{ selectedCount }}）</template>
        </el-button>
        <div v-if="op.hint && op.hint()" class="kpi-sub op-hint">{{ op.hint() }}</div>
      </el-card>
    </div>

    <!-- 3 处置记录 -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>
        <div class="card-head-row">
          <span>{{ t('emg.timeline') }}</span>
          <el-button v-if="timeline.length" link type="primary" @click="timeline = []">{{ t('emg.clearLog') }}</el-button>
        </div>
      </template>
      <div v-if="!timeline.length" class="kpi-sub">{{ t('emg.noLog') }}</div>
      <div v-for="(e, i) in timeline" :key="i" class="tl-row" :class="{ 'tl-warn': !e.ok }">
        <span class="kpi-sub">{{ e.time }}</span>
        <span class="tl-op">{{ e.op }}</span>
        <span>{{ e.text }}</span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sse, getRiskState, riskHalt, riskResume, riskCancelAll, riskCloseAll, getRiskExposure, getRiskCloseResult, getRiskCloseResultsRecent, apiErr } from '../api'
import { selectedIds, canResume } from '../utils/emergency'

const { t } = useI18n()
const state = ref({})
const haltTs = ref(null)
const accounts = ref([])
const timeline = ref([])
const nowTs = ref(Math.floor(Date.now() / 1000))
let timer = null

const halted = computed(() => !!state.value.halted)
const selectedCount = computed(() => selectedIds(accounts.value).length)

const resumeWait = () => {
  if (!halted.value || !haltTs.value) return ''
  const left = 300 - (nowTs.value - haltTs.value)
  if (left <= 0) return ''
  const m = Math.floor(left / 60)
  const s = left % 60
  return t('emg.resumeWait', { time: m + ':' + String(s).padStart(2, '0') })
}

const ops = computed(() => [
  {
    key: 'cancel', name: 'emg.opCancel', desc: 'emg.opCancelDesc',
    btn: 'emg.opCancelBtn', btnType: 'warning', plain: false, withCount: true,
    disabled: () => !selectedCount.value, run: onCancelAll,
  },
  {
    key: 'halt', name: 'emg.opHalt', desc: 'emg.opHaltDesc',
    btn: 'emg.opHaltBtn', btnType: 'danger', plain: false, withCount: false,
    disabled: () => halted.value, run: onHalt,
  },
  {
    key: 'close', name: 'emg.opClose', desc: 'emg.opCloseDesc',
    btn: 'emg.opCloseBtn', btnType: 'danger', plain: true, withCount: true,
    disabled: () => !selectedCount.value, run: onCloseAll,
  },
  {
    key: 'resume', name: 'emg.opResume', desc: 'emg.opResumeDesc',
    btn: 'emg.opResumeBtn', btnType: 'success', plain: true, withCount: false,
    disabled: () => !canResume(halted.value, haltTs.value, nowTs.value),
    run: onResume, hint: resumeWait,
  },
])

const fmtResult = r => {
  if (!r) return ''
  // 批 171s：首轮 pending/no_quote/retrying 清单（后端 _dispatch_risk_cmd 顶层聚合透传）
  const lists = []
  if (countOf(r.pending)) lists.push(t('emg.finalPending', { n: countOf(r.pending) }))
  if (countOf(r.no_quote)) lists.push(t('emg.finalNoQuote', { n: countOf(r.no_quote) }))
  if (r.retrying === true || countOf(r.retrying)) lists.push(t('emg.finalRetrying'))
  if (r.status === 'done') {
    const parts = [...lists]
    for (const [tid, ack] of Object.entries(r.results || {})) {
      // 批 171s：worker2 主回执改为扁平 dict（无 result 子 dict）——旧三层格式仍兼容
      const res = ack.result || ack
      if (res.cancel !== undefined) {
        parts.push(t('emg.rCancelled', { n: res.cancel?.requested ?? 0, f: res.cancel?.failed ?? 0 }))
        const c = res.close || {}
        if (c.skipped_t1?.length) parts.push(t('emg.rSkipped', { n: c.skipped_t1.length }))
        if (c.rejected?.length) parts.push(t('emg.rRejected', { n: c.rejected.length }))
        parts.push(t('emg.rRemain', { n: c.positions_remain ?? '?' }))
      } else if (res.requested !== undefined) {
        parts.push(t('emg.rCancelled', { n: res.requested, f: res.failed }))
      } else if (res.closed_requested !== undefined) {
        // 扁平主回执（本批）：closed_requested / positions_remain / retrying / interrupted
        parts.push(t('emg.finalClosed', { n: res.closed_requested }))
        parts.push(t('emg.rRemain', { n: res.positions_remain ?? '?' }))
        if (res.retrying === true) parts.push(t('emg.finalRetrying'))
        if (res.interrupted === true) parts.push(t('emg.finalInterrupted'))
      } else if (res.error) {
        parts.push(res.error + (res.detail ? ' — ' + res.detail : ''))
      }
    }
    return parts.join('；') || 'ok'
  }
  return [t('emg.rDispatched', { n: r.dispatched ?? 0 }), ...lists].join('；')
}

const addTl = (op, text, ok = true) => {
  const d = new Date()
  timeline.value.unshift({ time: d.toTimeString().slice(0, 8), op, text, ok })
}

// ── 批 171s：急停终局可见（零内容帧 SSE → 鉴权端点重拉，清单绝不在帧里）──
// 三路取数：①onMounted 拉 recent（页面刷新/晚开无内存 req_ids 仍可查）
// ②收到 emergency_close_final 零内容帧重拉 recent ③onCloseAll 按 req_ids 短轮询单查端点
const countOf = v => Array.isArray(v) ? v.length : (typeof v === 'number' ? v : 0)
const fmtFinal = (id, r) => {
  const parts = [t('emg.finalTitle', { id })]
  // 批 171s（编码审 D P2-c）：失败/异常终局优先展示，绝不误显示「全部平仓完成」
  // —— TD 断线（error=td_disconnected）或非 done 的 final_reason（重试用尽/系统断流）。
  if (r?.error) {
    parts.push(r.error + (r.detail ? ' — ' + r.detail : ''))
    return parts.join('；')
  }
  if (countOf(r?.pending)) parts.push(t('emg.finalPending', { n: countOf(r.pending) }))
  if (countOf(r?.no_quote)) parts.push(t('emg.finalNoQuote', { n: countOf(r.no_quote) }))
  // worker2 的 :final 里 retrying 是 bool 标志（非清单）——兼容 bool/非空 list 两形态
  if (r?.retrying === true || countOf(r?.retrying)) parts.push(t('emg.finalRetrying'))
  if (countOf(r?.closed_requested)) parts.push(t('emg.finalClosed', { n: countOf(r.closed_requested) }))
  if (r?.interrupted) parts.push(t('emg.finalInterrupted'))
  const fr = String(r?.final_reason || '')
  if (fr && fr !== 'final:done') {          // 非正常终局：显因，不追加「完成」
    parts.push(t('emg.finalReason', { r: fr }))
    return parts.join('；')
  }
  if (parts.length === 1) parts.push(t('emg.finalClean'))
  return parts.join('；')
}
const recentSeen = new Set()   // 防三路上屏同一 req_id 重复
const renderFinal = (id, doc) => {
  if (!id || recentSeen.has(id)) return
  recentSeen.add(id)
  addTl(t('emg.finalRecent'), fmtFinal(id, doc), true)
}
const loadRecent = async () => {
  try {
    const d = await getRiskCloseResultsRecent()
    for (const item of (d?.items || [])) renderFinal(item?.req_id, item)
  } catch { /* recent 端点失败不阻塞页面（轮询 + 单查仍可用） */ }
}

let finalPollTimer = null
const finalPollIds = ref([])
let finalPollTicks = 0
const FINAL_POLL_MAX_TICKS = 90   // 2s × 90 = 180s（覆盖 close_all_deadline_s 120s 余量）
const stopFinalPoll = () => {
  if (finalPollTimer) { clearInterval(finalPollTimer); finalPollTimer = null }
}
const pollFinals = async () => {
  if (!finalPollIds.value.length) { stopFinalPoll(); return }
  finalPollTicks += 1
  if (finalPollTicks > FINAL_POLL_MAX_TICKS) { stopFinalPoll(); finalPollIds.value = []; return }
  for (const id of [...finalPollIds.value]) {
    try {
      const r = await getRiskCloseResult(id)
      if (r?.status === 'final') {
        renderFinal(id, r)
        finalPollIds.value = finalPollIds.value.filter(x => x !== id)
      }
    } catch { /* 单次查询失败留待下轮——:final 键 TTL 30min，不丢 */ }
  }
  if (!finalPollIds.value.length) stopFinalPoll()
}
const startFinalPoll = (ids) => {
  finalPollIds.value = [...new Set([...finalPollIds.value, ...ids])].filter(Boolean)
  finalPollTicks = 0
  if (!finalPollTimer) finalPollTimer = setInterval(pollFinals, 2000)
}
const onSseFrame = (ev) => {
  if (ev.event !== 'data' || ev.data?.type !== 'emergency_close_final') return
  loadRecent()   // 零内容信号帧不带清单/req_id——重拉 recent 端点取权威清单
}
let sseUnsub = null

const load = async () => {
  try {
    const [s, e] = await Promise.all([getRiskState(), getRiskExposure()])
    state.value = s
    haltTs.value = e.halted_ts
    const items = e.items || []
    if (!accounts.value.length) {
      accounts.value = items.map(a => ({ ...a, checked: !!a.enabled }))   // 缺省：已启用全选
    } else {
      accounts.value = accounts.value.map(old => {
        const n = items.find(x => x.account_id === old.account_id)
        return n ? { ...n, checked: old.checked && !!n.enabled } : old
      })
    }
  } catch (e) { console.error(e) }
}

async function onCancelAll () {
  try {
    await ElMessageBox.confirm(t('emg.confirmCancelAll'), { type: 'warning' })
    const r = await riskCancelAll(selectedIds(accounts.value))
    addTl(t('emg.opCancel'), fmtResult(r), r.status === 'done')
    await load()
  } catch (e) {
    // 2026-11-09：失败必须上屏（原 catch 静默 → 用户看到「点了没反应」）
    if (e?.response || e?.code) {
      const msg = apiErr(e, t('common.failed'))
      ElMessage.error(msg)
      addTl(t('emg.opCancel'), msg, false)
    }
  }
}
async function onHalt () {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt') + ' ' + t('emg.haltGlobalNote'), { type: 'warning' })
    const r = await riskHalt()
    addTl(t('emg.opHalt'), t('risk.halted') + '；' + fmtResult(r.cancel), true)
    await load()
  } catch (e) {
    if (e?.response || e?.code) {
      const msg = apiErr(e, t('common.failed'))
      ElMessage.error(msg)
      addTl(t('emg.opHalt'), msg, false)
    }
  }
}
async function onCloseAll () {
  try {
    const { value } = await ElMessageBox.prompt(
      t('risk.closeAllPromptTip'), t('risk.closeAllPromptTitle'),
      { type: 'warning', confirmButtonText: t('risk.closeAll') })
    if (value?.trim() !== 'CONFIRM') { ElMessage.warning(t('risk.resumeMismatch')); return }
    const r = await riskCloseAll(selectedIds(accounts.value))
    addTl(t('emg.opClose'), fmtResult(r), r.status === 'done')
    const ids = Object.values(r?.req_ids || {})
    if (ids.length) {
      addTl(t('emg.opClose'), t('emg.finalWaiting'), true)
      startFinalPoll(ids)   // ③按返回 req_ids 短轮询单查端点（异常态无 SSE 帧时兜底）
    }
    await load()
  } catch (e) {
    if (e?.response || e?.code) {
      const msg = apiErr(e, t('common.failed'))
      ElMessage.error(msg)
      addTl(t('emg.opClose'), msg, false)
    }
  }
}
async function onResume () {
  try {
    const { value } = await ElMessageBox.prompt(
      t('risk.resumePromptTip'), t('risk.resumePromptTitle'),
      { type: 'warning', confirmButtonText: t('risk.resume') })
    if (value?.trim() !== 'RESUME') { ElMessage.warning(t('risk.resumeMismatch')); return }
    await riskResume()
    addTl(t('emg.opResume'), t('risk.resumed'), true)
    await load()
  } catch (e) {
    if (e?.response || e?.code) {
      const msg = apiErr(e, t('common.failed'))
      ElMessage.error(msg)
      addTl(t('emg.opResume'), msg, false)
    }
  }
}

onMounted(async () => {
  await load()
  loadRecent()                              // ①页面刷新/晚开无内存 req_ids 仍可查已有终局
  sseUnsub = sse.subscribe(onSseFrame)       // ②零内容信号帧 → 重拉 recent
  timer = setInterval(() => {
    nowTs.value = Math.floor(Date.now() / 1000)
    load()
  }, 5000)
})
onUnmounted(() => {
  clearInterval(timer)
  stopFinalPoll()
  if (sseUnsub) sseUnsub()
})
</script>

<style scoped>
.card-head-row { display: flex; justify-content: space-between; align-items: center; }
.scope-list { display: flex; flex-direction: column; gap: var(--sp-2); }
.scope-meta { margin-left: var(--sp-2); }
.op-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--sp-3); margin-top: var(--sp-3); }
.op-name { font-weight: 600; }
.op-sub { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); min-height: 32px; }
.op-btn { width: 100%; margin-top: var(--sp-2); }
.op-hint { margin-top: var(--sp-1); }
.tl-row { display: flex; gap: var(--sp-2); align-items: baseline; }
.tl-op { min-width: 60px; }
.tl-warn { color: var(--critical); }
.dot-live { display: inline-block; width: var(--dot); height: var(--dot); border-radius: 50%;
  background: var(--critical); margin-left: var(--sp-1); }
</style>
