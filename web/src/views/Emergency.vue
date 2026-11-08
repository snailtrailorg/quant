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
          <span v-if="a.unregistered" class="kpi-sub">（{{ t('emg.unregistered') }}）</span>
          <span v-else-if="!a.enabled" class="kpi-sub">（{{ t('emg.disabledAccount') }}）</span>
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
import { getRiskState, riskHalt, riskResume, riskCancelAll, riskCloseAll, getRiskExposure } from '../api'
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
  if (r.status === 'done') {
    const parts = []
    for (const [tid, ack] of Object.entries(r.results || {})) {
      const res = ack.result || {}
      if (res.cancel !== undefined) {
        parts.push(t('emg.rCancelled', { n: res.cancel?.requested ?? 0, f: res.cancel?.failed ?? 0 }))
        const c = res.close || {}
        if (c.skipped_t1?.length) parts.push(t('emg.rSkipped', { n: c.skipped_t1.length }))
        if (c.rejected?.length) parts.push(t('emg.rRejected', { n: c.rejected.length }))
        parts.push(t('emg.rRemain', { n: c.positions_remain ?? '?' }))
      } else if (res.requested !== undefined) {
        parts.push(t('emg.rCancelled', { n: res.requested, f: res.failed }))
      } else if (res.error) {
        parts.push(res.error + (res.detail ? ' — ' + res.detail : ''))
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
  } catch (e) { if (e?.response) console.error(e) }
}
async function onHalt () {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt') + ' ' + t('emg.haltGlobalNote'), { type: 'warning' })
    const r = await riskHalt()
    addTl(t('emg.opHalt'), t('risk.halted') + '；' + fmtResult(r.cancel), true)
    await load()
  } catch (e) { if (e?.response) console.error(e) }
}
async function onCloseAll () {
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
async function onResume () {
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
