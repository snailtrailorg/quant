<template>
  <div>
    <!-- 批 171o：紧急处置页（用户四轮定稿——状态操作分离/复选账号组/四级递进操作带）。
         无页签单工作区（D24 合规裁定：页面无标题→内容卡卡头）。 -->
    <!-- ① 全局状态条 -->
    <el-card>
      <template #header>{{ t('emg.title') }}</template>
      <el-alert :title="halted ? t('emg.halted') : t('emg.normal')"
                :type="halted ? 'error' : 'success'" show-icon :closable="false"
                style="margin-bottom: var(--sp-3)" />
      <el-descriptions :column="3" border>
        <el-descriptions-item :label="t('emg.haltReason')">{{ state.reason || '—' }}</el-descriptions-item>
        <el-descriptions-item :label="t('emg.haltDuration')">
          <span v-if="halted">{{ haltDurationText(haltTs, nowTs) }}</span><span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('emg.liveRunners')">{{ runningCount }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- ② 处置范围：通道账号复选组（默认全选） -->
    <el-card style="margin-top: var(--sp-3)">
      <template #header>{{ t('emg.scope') }}</template>
      <div style="display:flex;flex-direction:column;gap:var(--sp-2)">
        <el-checkbox v-for="a in accounts" :key="a.account_id" v-model="a.checked"
                     :disabled="!a.running">
          <span>{{ a.name }}</span>
          <span class="kpi-sub" style="margin-left:var(--sp-2)">
            {{ t('emg.working') }}: {{ a.working_orders == null ? '—' : (a.working_orders < 0 ? t('emg.unknown') : a.working_orders) }}
            · {{ t('emg.posValue') }}: {{ a.position_value == null ? t('emg.unknown') : '¥' + Number(a.position_value).toLocaleString() }}
          </span>
          <span v-if="(a.working_orders || 0) > 0" class="dot-live" :title="t('emg.hasWorking')" />
          <span v-if="!a.running" class="kpi-sub">（{{ t('emg.notRunning') }}）</span>
        </el-checkbox>
        <el-checkbox v-model="allChecked" :indeterminate="someChecked"
                     style="margin-top:var(--sp-1)">{{ t('emg.selectAll') }}</el-checkbox>
        <div class="kpi-sub unconn-row">
          {{ t('emg.unconnected') }}：Binance · OKX（{{ t('emg.apiNotOpen') }}）
        </div>
      </div>
    </el-card>

    <!-- ③ 操作带：四级递进（用户裁定——每级包含前级） -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:var(--sp-3);margin-top:var(--sp-3)">
      <el-card class="op-card">
        <div class="op-name">{{ t('emg.opCancel') }}</div>
        <div class="op-sub">{{ t('emg.opCancelDesc') }}</div>
        <div class="op-sub kpi-sub">{{ t('emg.level1') }}</div>
        <el-button type="warning" style="width:100%;margin-top:var(--sp-2)"
                   :disabled="!selectedCount" @click="onCancelAll">
          {{ t('emg.opCancelBtn') }}（{{ selectedCount }}）</el-button>
      </el-card>
      <el-card class="op-card">
        <div class="op-name">{{ t('emg.opHalt') }}</div>
        <div class="op-sub">{{ t('emg.opHaltDesc') }}</div>
        <div class="op-sub kpi-sub">{{ t('emg.level2') }}</div>
        <el-button type="danger" style="width:100%;margin-top:var(--sp-2)"
                   :disabled="halted" @click="onHalt">{{ t('emg.opHaltBtn') }}</el-button>
      </el-card>
      <el-card class="op-card">
        <div class="op-name">{{ t('emg.opClose') }}</div>
        <div class="op-sub">{{ t('emg.opCloseDesc') }}</div>
        <div class="op-sub kpi-sub">{{ t('emg.level3') }}</div>
        <el-button type="danger" plain style="width:100%;margin-top:var(--sp-2)"
                   :disabled="!selectedCount" @click="onCloseAll">
          {{ t('emg.opCloseBtn') }}（{{ selectedCount }}）</el-button>
      </el-card>
      <el-card class="op-card">
        <div class="op-name">{{ t('emg.opResume') }}</div>
        <div class="op-sub">{{ t('emg.opResumeDesc') }}</div>
        <div class="op-sub kpi-sub">{{ t('emg.level4') }}</div>
        <el-button type="success" plain style="width:100%;margin-top:var(--sp-2)"
                   :disabled="!canResume(halted, haltTs, nowTs)" @click="onResume">
          {{ t('emg.opResumeBtn') }}</el-button>
      </el-card>
    </div>

    <!-- ④ 处置回执时间线 -->
    <el-card style="margin-top: var(--sp-3)" v-if="timeline.length">
      <template #header>{{ t('emg.timeline') }}</template>
      <div v-for="(e, i) in timeline" :key="i" class="tl-row" :class="{ 'tl-warn': !e.ok }">
        <span class="kpi-sub">{{ e.time }}</span>
        <span class="tl-op">{{ e.op }}</span>
        <span>{{ e.text }}</span>
      </div>
    </el-card>

    <!-- ⑤ 安全阀说明 -->
    <div class="kpi-sub safety-bar">
      {{ t('emg.safety') }} · {{ t('emg.dataFresh') }}
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRiskState, riskHalt, riskResume, riskCancelAll, riskCloseAll, getRiskExposure } from '../api'
import { selectedIds, canResume, haltDurationText } from '../utils/emergency'

const { t } = useI18n()
const state = ref({})
const haltTs = ref(null)
const accounts = ref([])
const timeline = ref([])
const nowTs = ref(Math.floor(Date.now() / 1000))
let timer = null

const halted = computed(() => !!state.value.halted)
const runningCount = computed(() => accounts.value.filter(a => a.running).length)
const selectedCount = computed(() => selectedIds(accounts.value).length)
const allChecked = computed({
  get: () => accounts.value.every(a => a.checked),
  set: v => accounts.value.forEach(a => { a.checked = v }),
})
const someChecked = computed(() => {
  const c = accounts.value.filter(a => a.checked).length
  return c > 0 && c < accounts.value.length
})

const fmtResult = r => {
  if (!r) return ''
  if (r.status === 'done') {
    const parts = []
    for (const [tid, ack] of Object.entries(r.results || {})) {
      const res = ack.result || {}
      if (res.cancel !== undefined) {
        parts.push(`${tid}: ${t('emg.rCancelled', { n: res.cancel?.requested ?? 0, f: res.cancel?.failed ?? 0 })}`)
        const c = res.close || {}
        if (c.skipped_t1?.length) parts.push(t('emg.rSkipped', { n: c.skipped_t1.length }))
        if (c.rejected?.length) parts.push(t('emg.rRejected', { n: c.rejected.length }))
        parts.push(t('emg.rRemain', { n: c.positions_remain ?? '?' }))
      } else if (res.requested !== undefined) {
        parts.push(`${tid}: ${t('emg.rCancelled', { n: res.requested, f: res.failed })}`)
      } else if (res.error) {
        parts.push(`${tid}: ${res.error}${res.detail ? ' — ' + res.detail : ''}`)
      }
    }
    return parts.join('；') || 'ok'
  }
  return t('emg.rDispatched', { n: r.dispatched ?? 0 })
}

const addTl = (op, text, ok = true) => {
  const d = new Date()
  timeline.value.unshift({ time: d.toTimeString().slice(0, 8), op, text, ok })
}

const load = async () => {
  try {
    const [s, e] = await Promise.all([getRiskState(), getRiskExposure()])
    state.value = s
    haltTs.value = e.halted_ts
    const items = e.items || []
    if (!accounts.value.length) {
      accounts.value = items.map(a => ({ ...a, checked: true }))   // 默认全选
    } else {
      // 刷新数据保勾选态
      accounts.value = accounts.value.map(old => {
        const n = items.find(x => x.account_id === old.account_id)
        return n ? { ...n, checked: old.checked } : old
      })
    }
  } catch (e) { console.error(e) }
}

const onCancelAll = async () => {
  try {
    await ElMessageBox.confirm(t('emg.confirmCancelAll'), { type: 'warning' })
    const r = await riskCancelAll(selectedIds(accounts.value))
    addTl(t('emg.opCancel'), fmtResult(r), r.status === 'done')
    await load()
  } catch (e) { if (e?.response) console.error(e) }
}
const onHalt = async () => {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt') + ' ' + t('emg.haltGlobalNote'), { type: 'warning' })
    const r = await riskHalt()
    addTl(t('emg.opHalt'), t('risk.halted') + '；' + fmtResult(r.cancel), true)
    await load()
  } catch (e) { if (e?.response) console.error(e) }
}
const onCloseAll = async () => {
  try {
    const { value } = await ElMessageBox.prompt(
      t('risk.closeAllPromptTip'), t('risk.closeAllPromptTitle'),
      { type: 'warning', confirmButtonText: t('risk.closeAll') })
    if (value?.trim() !== 'CONFIRM') { ElMessage.warning(t('risk.resumeMismatch')); return }
    const r = await riskCloseAll(selectedIds(accounts.value))
    addTl(t('emg.opClose'), fmtResult(r), r.status === 'done')
    await load()
  } catch (e) { if (e?.response) console.error(e) }
}
const onResume = async () => {
  try {
    const { value } = await ElMessageBox.prompt(
      t('risk.resumePromptTip'), t('risk.resumePromptTitle'),
      { type: 'warning', confirmButtonText: t('risk.resume') })
    if (value?.trim() !== 'RESUME') { ElMessage.warning(t('risk.resumeMismatch')); return }
    await riskResume()
    addTl(t('emg.opResume'), t('risk.resumed'), true)
    await load()
  } catch (e) { if (e?.response) console.error(e) }
}

onMounted(async () => {
  await load()
  timer = setInterval(() => {
    nowTs.value = Math.floor(Date.now() / 1000)
    load()
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.op-card { --pad: var(--sp-3); }
.op-name { font-weight: 600; font-size: var(--fs-body); }
.op-sub { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); }
.tl-row { display: flex; gap: var(--sp-2); align-items: baseline; }
.tl-warn { color: var(--critical); }
.dot-live { display:inline-block; width: var(--dot); height: var(--dot); border-radius:50%;
  background: var(--critical); margin-left: var(--sp-1); }
</style>

<style scoped>
.tl-op { min-width: 60px; }
.safety-bar { margin-top: var(--sp-3); padding: var(--sp-2); background: var(--bg-canvas);
  border-radius: var(--sp-1); }
</style>

<style scoped>
.unconn-row { border-top: var(--border-w, 1px) solid var(--border); padding-top: var(--sp-2); }
</style>
