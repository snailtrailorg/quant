<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('liveTask.title') }}</span>
        <div style="display: flex; gap: 8px; align-items: center">
          <ColumnSettings storage-key="cols.live-tasks" :columns="taskColDefs" v-model:visible="taskVisible" />
          <IconBtn v-if="canLive" :icon="Plus" :title="t('liveTask.create')" @click="openCreate" />
        </div>
      </div>
    </template>
    <!-- 批17 17A：列宽拖拽+持久化 -->
    <!-- 批113：row-key + expand-row-keys 受控展开（Pool.vue 同款）——支持 query.freeze 深链程序化展开该任务行 -->
    <TableShell :data="tasks" storage-key="live-tasks" :row-key="r => r.id" :expand-row-keys="expanded" @expand-change="onExpand">
      <el-table-column v-if="colOn('id')" prop="id" label="ID" width="80" />
      <!-- 批86-UI：「个股」从操作列移入名称列（router-link）——原样是个 6 字文本按钮摆在启停之间，
           语义是「跳到标的详情」而非「对任务做什么」，混在动作组里既挤又容易被误读成任务动作。 -->
      <el-table-column :label="t('common.name')" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <router-link v-if="row.symbol" :to="`/stock/${row.symbol}`">{{ row.name }}</router-link>
          <span v-else>{{ row.name }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('strategy_id')" prop="strategy_id" :label="t('liveTask.strategy')" min-width="120" show-overflow-tooltip />
      <el-table-column v-if="colOn('symbol')" prop="symbol" :label="t('common.symbol')" min-width="100" show-overflow-tooltip />
      <!-- P1-5（06 B#5）：md_mode/行情 lag/bars 消费/frozen——活着吗/新鲜吗/冻没冻直答 -->
      <el-table-column v-if="colOn('md_mode')" prop="md_mode" :label="t('liveTask.mdMode')" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.md_mode === 'hub' ? 'primary' : 'info'">{{ row.md_mode }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('lag')" prop="lag" :label="t('liveTask.lag')" width="90" class-name="num">
        <template #default="{ row }">
          <span :style="{ color: (row.lag ?? 999) > 5 ? 'var(--warn)' : 'var(--success)' }">{{ row.lag != null ? row.lag.toFixed(1) + 's' : '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('bars')" prop="bars" :label="t('liveTask.bars')" width="90" class-name="num" />
      <el-table-column v-if="colOn('status')" prop="status" :label="t('common.status')" width="110">
        <template #default="{ row }">
          <span style="display:inline-flex; align-items:center; gap:4px"><StatusTag :value="row.status" />{{ row.frozen ? '❄' : '' }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('account_id')" prop="account_name" :label="t('liveTask.account')" min-width="120" show-overflow-tooltip />
      <el-table-column v-if="colOn('initial_capital')" prop="initial_capital" :label="t('liveTask.capital')" min-width="120" />
      <!-- 批16：+创建时间/心跳年龄（后端已返回未显示） -->
      <el-table-column v-if="colOn('created_at')" prop="created_at" :label="t('common.createdAt')" min-width="220">
        <template #default="{ row }">{{ fmtTime.full(row.created_at) }}</template>
      </el-table-column>
      <el-table-column v-if="colOn('hb_age')" prop="hb_age" :label="t('cols.heartbeatAge')" min-width="147" class-name="num">
        <template #default="{ row }">{{ fmtAge(row.hb_age_s) }}</template>
      </el-table-column>
            <el-table-column type="expand">
        <template #default="{ row }">
          <div style="padding: var(--sp-2) 16px">
            <!-- wd-20 §1.5 方案 A：自愈时间线（task_logs 过滤渲染 + 行内事实字段） -->
            <div style="font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: 6px">{{ t('liveTask.selfHeal') }}:</div>
            <div style="font-size: var(--fs-foot); font-family: var(--font-num)">
              {{ t('liveTask.mdMode') }}: {{ row.md_mode || '—' }} | {{ t('liveTask.lag') }}: {{ row.lag_s ?? row.lag ?? '—' }}s | {{ t('liveTask.bars') }}: {{ row.bars ?? '—' }} | {{ t('common.status') }}: {{ row.status }}
              | {{ t('liveTask.nRestarts') }}: {{ restartCount(row) }} | {{ t('liveTask.lastExit') }}: {{ lastExit(row) }}
            </div>
            <div v-if="row._timeline?.length" style="margin-top: var(--sp-2)">
              <div style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ t('liveTask.recentLogs') }}:</div>
              <div v-for="(l, i) in row._timeline.slice(0, 8)" :key="i" style="font-size: var(--fs-foot); font-family: var(--font-num); display: flex; gap: 8px">
                <span style="color: var(--text-secondary)">{{ fmtTime.s(l.ts) }}</span>
                <span :style="{ color: ['ERROR', 'error'].includes(l.level) ? 'var(--critical)' : ['WARNING', 'warning', 'WARN'].includes(l.level) ? 'var(--warn)' : 'inherit' }">{{ l.msg?.slice(0, 100) }}</span>
              </div>
            </div>

            <!-- 批 76b：冻结史（F1 freeze_event 持久账）——瞬时灯的对立面：
                 列表行的 ❄ 是 30s 轮询布尔（短冻结会整跳），此处是「冻过几次/谁解的/用哪种方式」的事实。
                 三态：null=无 read 权限（与空区分，防误读为「没冻过」）/ []=真没冻过 / [...]=有记录 -->
            <div style="margin-top: var(--sp-2); border-top: 1px solid var(--border-light); padding-top: var(--sp-2)">
              <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-bottom: 6px">
                {{ t('liveTask.freezeHistory') }}
                <span v-if="!row._freeze?.length" style="opacity: 0.7">{{ t('liveTask.freezeSince') }}</span>
              </div>
              <div v-if="row._freeze === null" style="font-size: var(--fs-foot); color: var(--text-secondary)">{{ t('liveTask.freezeNoPerm') }}</div>
              <div v-else-if="!row._freeze?.length" style="font-size: var(--fs-foot); color: var(--text-secondary)">{{ t('liveTask.freezeNone') }}</div>
              <div v-else style="font-size: var(--fs-foot); font-family: var(--font-num)">
                <div style="display: flex; gap: 8px; color: var(--text-secondary); margin-bottom: 4px">
                  <span style="min-width: 150px">{{ t('liveTask.colFrozenAt') }}</span>
                  <span style="min-width: 68px">{{ t('liveTask.colType') }}</span>
                  <span style="min-width: 92px">{{ t('liveTask.colWatermark') }}</span>
                  <span style="min-width: 150px">{{ t('liveTask.colUnfrozenAt') }}</span>
                  <span>{{ t('liveTask.colMethod') }}</span>
                </div>
                <div v-for="e in row._freeze" :key="e.id"
                     :class="e.unfrozen_at ? 'fz-closed' : 'fz-open'"
                     style="display: flex; gap: 8px; padding: 2px 0">
                  <span style="min-width: 150px">{{ fmtTime.s(e.frozen_at) }}</span>
                  <span style="min-width: 68px">{{ t(FT[e.freeze_type] || 'liveTask.ftUnknown') }}</span>
                  <span style="min-width: 92px">{{ e.watermark ?? '—' }} → {{ e.gap_target_ts ?? '—' }}</span>
                  <span style="min-width: 150px">{{ e.unfrozen_at ? fmtTime.s(e.unfrozen_at) : t('liveTask.inProgress') }}</span>
                  <span>
                    {{ e.unfrozen_at ? t(UM[e.unfreeze_method] || 'liveTask.umUnknown') : '⏱ ' + frozenFor(row) }}
                    <span v-if="e.operator" style="color: var(--text-secondary)"> · {{ e.operator }}</span>
                  </span>
                  <!-- 批 76b：解冻入口移入「进行中」行——判据是 F1 未闭合（持久），
                       而非 30s 瞬时灯，故短冻结也找得到入口；信息（原因/水位差/已冻时长）同屏可见 -->
                  <IconBtn v-if="!e.unfrozen_at && canLive" size="small" type="warning" :icon="Unlock"
                           :title="t('liveTask.unfreeze')" @click="onUnfreeze(row)" :disabled="navReadonly" />
                </div>
              </div>
            </div>
          </div>
        </template>
      </el-table-column>
<el-table-column prop="actions" :label="t('common.action')" width="200" fixed="right">
        <template #default="{ row }">
          <!-- 批86-UI：操作列全量对齐批21「全站圆形图标按钮」（Backtest 为同类样板）。
               改前是一半图标一半文本：解冻钮批76b 已按 IconBtn 做，启/停/个股/更多仍是批16 原生的
               el-button type=success|danger —— 那三个色是 EP 语义色，不是我们的金融令牌，
               而令牌门只数 px/hex/font-size 三维，结构上看不见 type= ⇒ 色漂移永不告警（批86 补第四维）。
               颜色语义收敛为三档：success=启 / danger=停·删 / warning=解冻 / ''(品牌蓝)=看·改。
               删除的强确认（输入任务名）保留，只是入口从「更多」弹窗改为直接点删除图标。 -->
          <IconBtn size="small" type="success" :icon="VideoPlay" :title="t('common.start')"
                   v-if="row.status !== 'running' && canLive" @click="onStart(row.id)" :disabled="navReadonly" />
          <IconBtn size="small" type="danger" :icon="VideoPause" :title="t('common.stop')"
                   v-if="row.status === 'running' && canLive" @click="onStop(row)" :disabled="navReadonly" />
          <!-- 批 76b：解冻钮判据由「30s 瞬时灯 row.frozen」改为「F1 未闭合事件」（持久）——
               灯会丢帧（短冻结整跳 ⇒ 想解冻却找不到入口）；与展开行同一 handler，无第二套语义。
               注：row.frozen 仍用于状态列的 ❄ 即时标记，两者用途不同（灯=此刻/账=历史）。 -->
          <IconBtn size="small" type="warning" :icon="Unlock" :title="t('liveTask.unfreeze')" v-if="row.status === 'running' && freezing(row).length && canLive" @click="onUnfreeze(row)" :disabled="navReadonly" />
          <!-- 查看结果：批 86-B 拆页后**从本页移除**。原批 86-UI 留的 disabled 占位
               （「不藏、不静默」）其前提是 paper 任务还混在本列表里；现在本列表只含
               mode='live'（后端过滤），而权益曲线是 paper_trade_log 的产物——实盘任务
               没有这个能力，常显 disabled 就是本仓在录的「死按钮」缺陷类。真按钮在纸上交易页。
               编辑（批 86-B 端点 PUT /api/live-task/{tid} 已落）：仅 name/资金/params，
               身份字段（strategy_id/symbol/account_id/mode）不可改=后端 400。 -->
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')"
                   v-if="row.status !== 'running' && canLive" @click="openEdit(row)" :disabled="navReadonly" />
          <!-- 删除：入口即语义（原藏在名为「更多」的弹窗里，且弹窗内只有删除这一件事）。
               running 中不允许删——后端会拒（任务在跑先停），故此处直接不显示，避免 403 死胡同。 -->
          <IconBtn size="small" type="danger" :icon="Delete" :title="t('common.delete')"
                   v-if="row.status !== 'running' && canLive" @click="openDelete(row)" :disabled="navReadonly" />
        </template>
      </el-table-column>
    </TableShell>

    <!-- 批86-UI：原「更多」弹窗拆除。它里面只有一件事（删除），名字叫「更多」名不副实——
         对照 Backtest 的「更多」是两件事（终止/删除）。现在删除是操作列的红色垃圾桶图标，
         入口即语义；这里只剩强确认本身（输入任务名，站内最强确认），标题也改为直陈「删除任务」。 -->
    <el-dialog v-model="delVisible" :title="t('liveTask.deleteTitle')" width="420px" :close-on-click-modal="false">
      <el-form @submit.prevent>
        <el-form-item :label="t('liveTask.deletePromptTip', { name: delRow?.name })">
          <el-input v-model="deleteConfirmName" :placeholder="delRow?.name" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="delVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :disabled="!delRow || deleteConfirmName.trim() !== delRow.name" @click="onDelete(delRow)">{{ t('common.delete') }}</el-button>
      </template>
    </el-dialog>

    <!-- 创建实盘任务弹窗 -->
    <el-dialog v-model="dialogVisible" :title="t('liveTask.create')" width="720px" :close-on-click-modal="false">
      <el-form :model="form" label-width="120px" v-loading="saving">
        <el-form-item :label="t('liveTask.taskName')">
          <el-input v-model="form.name" :placeholder="t('liveTask.phName')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.strategy')">
          <el-select v-model="form.strategy_id" :placeholder="t('liveTask.phStrategy')" style="width: 100%" @change="onStrategyChange">
            <el-option v-for="s in strategies" :key="s.id" :label="`${s.name} (${s.id})`" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('common.symbol')">
          <!-- 链条打磨#20：标的搜索下拉（asset_static_info；此前纯手输无校验） -->
          <el-select v-model="form.symbol" filterable remote :remote-method="searchSymbols"
                     :loading="symbolSearching" :placeholder="t('liveTask.phSymbol')" style="width: 100%">
            <el-option v-for="sym in symbolOptions" :key="sym" :label="sym" :value="sym" />
          </el-select>
        </el-form-item>

        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="parameterDefs.length" :defs="parameterDefs" v-model="form.params" />
        <div v-else style="color: var(--text-secondary); font-size: var(--fs-foot); padding-left: 120px">
          {{ t('liveTask.selectStrategyFirst') }}
        </div>

        <el-divider content-position="left">{{ t('liveTask.account') }}</el-divider>
        <el-form-item :label="t('liveTask.accountId')">
          <el-select v-model="form.account_id" :placeholder="t('liveTask.phAccountId')" style="width: 100%">
            <el-option v-for="v in accounts" :key="v.id" :label="`${v.name} (${v.id})`" :value="v.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="form.initial_capital" v-bind="CAPITAL_INPUT" />   <!-- 批36b-β：补 max（三胞胎单源） -->
        </el-form-item>
      
        <!-- 批 6b：md_mode 单模式（hub），创建不再可选——direct 2026-09-01 退役 --></el-form>
      <template #footer>
        <el-button type="primary" @click="dialogVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="save" :loading="saving" :disabled="navReadonly">{{ t('liveTask.createBtn') }}</el-button>
      </template>
    </el-dialog>

    <!-- 批 86-B：编辑任务弹窗——身份字段（策略/标的/账户/mode）不出现于此（不可改=删除重建），
         参数定义取自策略当前定义（与后端「按策略快照校验」可能存在策略被改后的窗口差，见 saveEdit 注释）。 -->
    <el-dialog v-model="editVisible" :title="t('paperTrade.editTitle')" width="720px" :close-on-click-modal="false">
      <el-form :model="editForm" label-width="120px" v-loading="saving">
        <el-form-item :label="t('liveTask.taskName')">
          <el-input v-model="editForm.name" :placeholder="t('liveTask.phName')" />
        </el-form-item>
        <el-form-item :label="t('liveTask.initialCapital')">
          <el-input-number v-model="editForm.initial_capital" v-bind="CAPITAL_INPUT" />
        </el-form-item>
        <el-divider content-position="left">{{ t('liveTask.taskParams') }}</el-divider>
        <ParameterForm v-if="editDefs.length" :defs="editDefs" v-model="editForm.params" />
        <div v-else style="color: var(--text-secondary); font-size: var(--fs-foot)">
          {{ t('paperTrade.noParamDefs') }}
        </div>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="editVisible = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit" :loading="saving" :disabled="navReadonly">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted, inject, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { getLiveTasks, createLiveTask, updateLiveTask, startLiveTask, stopLiveTask, deleteLiveTask, getFreezeEvents, unfreezeLiveTask, getStrategies, getInterfaces, apiErr } from '../api'
import { CAPITAL_INPUT } from '../utils/inputRanges'
import ParameterForm from '../components/ParameterForm.vue'
import StatusTag from '../components/StatusTag.vue'
import TableShell from '../components/TableShell.vue'
import ColumnSettings from '../components/ColumnSettings.vue'
import IconBtn from '../components/IconBtn.vue'
import { Plus, Unlock, VideoPlay, VideoPause, Edit, Delete } from '@element-plus/icons-vue'
import { fmtTime } from '../utils/fmtTime'

const router = useRouter()
const route = useRoute()
const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
// 批 77：实盘面动作（建/启/停/删任务、解冻）须 `live_control`——与后端 require_perm 同键。
// analyst 只持研究面 strategy_control（可读本页、可回测）⇒ 实盘按钮不显示（避免点了 403 的死胡同）。
const canPerm = inject('canPerm', () => true)
const canLive = computed(() => canPerm('live_control'))
const tasks = ref([])
const expanded = ref([])   // 批113：受控展开行 id 集（row-key=r=>r.id），深链 query.freeze 程序化展开
const strategies = ref([])
const accounts = ref([])
const symbolOptions = ref([])
const symbolSearching = ref(false)
const loadAccounts = async () => {
  // 批83a：拆表后实盘任务绑定的是交易账号族（域=trading_account）——旧 'trading' 是能力 token 不是域；
  // 仍按 trading 能力筛选（行为不变：纯行情账号不进出绑定下拉，live_task.account_id FK 指向 trading_account.id）
  try { accounts.value = await getInterfaces('trading_account', 'trading') || [] } catch { accounts.value = [] }
}
const searchSymbols = async (q) => {
  if (!q || q.length < 2) { symbolOptions.value = []; return }
  symbolSearching.value = true
  try {
    const r = await api.get('/sync/symbols/astock_daily', { params: { q, page: 1, size: 20 } })
    symbolOptions.value = (r.items || []).slice(0, 20).map(i => i.ts_code)
  } catch { symbolOptions.value = [] }
  finally { symbolSearching.value = false }
}
const dialogVisible = ref(false)
const saving = ref(false)
const parameterDefs = ref([])
const form = ref({
  name: '', strategy_id: '', symbol: '', params: {},
  account_id: '', initial_capital: 1000000,
})

// 批17 17B：列显隐（方案圈定——bars/md_mode/账户列低频默认隐；名称/操作/展开锁定不进 defs）
const taskColDefs = computed(() => [
  { key: 'id', label: 'ID' },
  { key: 'strategy_id', label: t('liveTask.strategy') },
  { key: 'symbol', label: t('common.symbol') },
  { key: 'md_mode', label: t('liveTask.mdMode'), hidden: true },
  { key: 'lag', label: t('liveTask.lag') },
  { key: 'bars', label: t('liveTask.bars'), hidden: true },
  { key: 'status', label: t('common.status') },
  { key: 'account_id', label: t('liveTask.account'), hidden: true },
  { key: 'initial_capital', label: t('liveTask.capital'), hidden: true },
  { key: 'created_at', label: t('common.createdAt') },
  { key: 'hb_age', label: t('cols.heartbeatAge') },
])
const taskVisible = ref([])
const colOn = k => taskVisible.value.includes(k)


const load = async () => {
  try { tasks.value = await getLiveTasks(); enrichTasks(); preloadFreeze() } catch { ElMessage.error(t('common.loadFailed')) }
}   // 盲审A-P2-8：load 后 enrich（start/stop 重建 tasks 后重启数不回落）
const loadStrategies = async () => {
  try {
    // 链条打磨#20：只列 backtest_verified 策略（三级开关第三级——未验证的选了也是 403 后置暴露）
    const all = await getStrategies()
    strategies.value = (all || []).filter(s => s.backtest_verified)
  } catch { strategies.value = [] }
}

const onStrategyChange = (sid) => {
  const s = strategies.value.find(x => x.id === sid)
  if (s?.params?.parameter_defs) {
    parameterDefs.value = s.params.parameter_defs
    form.value.params = {}
  } else {
    parameterDefs.value = []
    form.value.params = {}
  }
}

const openCreate = () => {
  form.value = { name: '', strategy_id: '', symbol: '', params: {}, account_id: '', initial_capital: 1000000 }
  parameterDefs.value = []
  dialogVisible.value = true
}

const save = async () => {
  if (!form.value.name || !form.value.strategy_id || !form.value.symbol || !form.value.account_id) {
    ElMessage.warning(t('liveTask.requiredHint')); return
  }
  saving.value = true
  try {
    await createLiveTask(form.value)
    ElMessage.success(t('common.createSuccess'))
    dialogVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.createFailed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

// 批 76b：解冻=人工接受当前数据状态（带洞窗/污染窗），**不重启任务**。
// 旧实现误走 stop+start ⇒ 记 `restart` 闭环（operator 空、方式错）+ 白白打断运行，已修正。
// ⚠️ 生效有延迟：后端只写 Valkey 请求键，worker 5s 钩子 GETDEL 消费后才闭环 F1。
// 故此处**不立即重拉冻结史**（拉了也仍是「进行中」= 误导），只给「已下发」提示；
// 闭环由 load() 的 30s 轮询 → preloadFreeze 对有未闭合事件的行重拉自然带出。
const onUnfreeze = async (row) => {
  try {
    await ElMessageBox.confirm(t('liveTask.confirmUnfreeze'), t('common.confirm'), { type: 'warning' })
    await unfreezeLiveTask(row.id)
    ElMessage.success(t('liveTask.unfreezeSent'))
  } catch (e) { if (e?.response) ElMessage.error(t('common.failed')) }
}
// 批 76b：冻结史惰性拉取（展开时 + 解冻后刷新）。三态：null=无权限 / []=空 / [...]=有
const loadFreeze = async (tid) => {
  const row = tasks.value.find(x => x.id === tid)
  if (!row) return
  try {
    const r = await getFreezeEvents(tid, 20)
    row._freeze = r?.events || []
  } catch (e) { row._freeze = e?.response?.status === 403 ? null : [] }
}
// 有未闭合事件（unfrozen_at 为 null）= 此刻真冻着——持久判据，不受 30s 瞬时灯丢帧影响
const freezing = row => (row._freeze || []).filter(e => !e.unfrozen_at)
// 批 76b：展开时惰性拉冻结史。EP `expand-change` 双重重载：多列展开表=`(row, T[])`，
// 树/单列=`(row, boolean)`。两种都要认（boolean=true 即「本行刚展开」，目标就是 row 自身）。
const onExpand = (row, expandedRows) => {
  let open
  if (Array.isArray(expandedRows)) open = expandedRows.some(r => r?.id === row?.id)
  else open = expandedRows === true
  // 批113：受控 expand-row-keys 需回写展开 id 集（Pool.vue 同款），深链/手点展开都走这里
  if (Array.isArray(expandedRows)) expanded.value = expandedRows.map(r => r.id)
  else if (open) expanded.value = [row.id]
  if (open && row._freeze === undefined) loadFreeze(row.id)
}
// 已冻结时长（秒）——进行中行显示；只算「现在还在冻」的那条
const frozenFor = (row) => {
  const e = freezing(row)[0]
  if (!e?.frozen_at) return ''
  const secs = Math.max(0, Math.floor((Date.now() - new Date(e.frozen_at).getTime()) / 1000))
  if (secs < 60) return `${secs} 秒`
  if (secs < 3600) return `${Math.floor(secs / 60)} 分 ${secs % 60} 秒`
  return `${Math.floor(secs / 3600)} 时 ${Math.floor((secs % 3600) / 60)} 分`
}
// 三类冻结 + 四值解冻方式的词条映射（枚举由 0123 CHECK 锁死）
const FT = { ts_gap: 'liveTask.ftTsGap', seq_gap: 'liveTask.ftSeqGap', untrusted: 'liveTask.ftUntrusted' }
const UM = { restart: 'liveTask.umRestart', auto_reconnect: 'liveTask.umAuto', manual_web: 'liveTask.umWeb', manual_im: 'liveTask.umIm' }
// 批 76b：running 行的冻结史预拉（操作列解冻钮的判据依赖它——不预拉则钮永不显示）。
// 只拉 running（stopped/error 不可能冻着），随 load 的 30s 轮询走，量=运行中任务数。
// 判据两条：① 从未拉过（undefined）② 当前有未闭合事件（要跟时长、也要发现「已解冻」）。
// ⚠️ 不能写成「无未闭合就拉」——那会让每个干净行每 30s 重复请求一次（放大 N 倍）。
const preloadFreeze = () => {
  tasks.value.filter(x => x.status === 'running' && (x._freeze === undefined || freezing(x).length))
    .forEach(x => loadFreeze(x.id))
}
const onStart = async (id) => {
  try { await startLiveTask(id); ElMessage.success(t('common.started')); load() }
  catch (e) { ElMessage.error(t('common.startFailed')) }
}
const onStop = async (row) => {
  // H5（01 P0#2/05 §5.8）：停止=影响面 confirm（确认强度对称于代价——原停止无确认、删除反有，倒挂修正）
  try {
    await ElMessageBox.confirm(t('liveTask.confirmStop'), t('common.confirm'), { type: 'warning' })
    await stopLiveTask(row.id); ElMessage.success(t('common.stopped')); load()
  } catch (e) { if (e !== 'cancel' && e?.message) ElMessage.error(t('common.stopFailed')); else if (e?.response) ElMessage.error(t('common.stopFailed')) }
}
// 批86-UI：删除确认（原批16「更多」弹窗的壳，现在只承载删除这一件事）
const delRow = ref(null)
const delVisible = computed({
  get: () => !!delRow.value,
  set: v => { if (!v) delRow.value = null },
})
const deleteConfirmName = ref('')
const openDelete = (row) => { delRow.value = row; deleteConfirmName.value = ''; delVisible.value = true }
const onDelete = async (row) => {
  if (!row) return
  try {
    await deleteLiveTask(row.id)
    ElMessage.success(t('common.deleteSuccess'))
    delRow.value = null
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

// 批 86-B：编辑任务（原批86-UI 预留注释移除——端点已落，连按钮一起上）。
// 可编辑=name/initial_capital/params；strategy_id/symbol/account_id/mode 是身份字段
// （systemd 单元名绑 tid、策略快照建任务时固化、账户决定风控预算归属），改=删掉重建，
// 后端对不可改字段显式 400 FIELD_IMMUTABLE（优于静默忽略）。
// 参数定义取自策略当前定义；若策略在任务创建后被改过，服务端按**策略快照**校验（真源），
// UI 定义与服务端不一致时以 400 PARAM_INVALID 为准——两处同错的窗口只在「策略建任务后被编辑」。
const editVisible = ref(false)
const editRow = ref(null)
const editDefs = ref([])
const editForm = ref({ name: '', initial_capital: 0, params: {} })
const openEdit = (row) => {
  editRow.value = row
  editForm.value = {
    name: row.name,
    initial_capital: row.initial_capital ?? 0,
    params: { ...(row.params || {}) },   // 现值回填（浅拷贝展开，防表格行被就地改）
  }
  const s = strategies.value.find(x => x.id === row.strategy_id)
  editDefs.value = s?.params?.parameter_defs || []
  editVisible.value = true
}
const saveEdit = async () => {
  if (!editForm.value.name?.trim()) { ElMessage.warning(t('paperTrade.nameRequired')); return }
  saving.value = true
  try {
    await updateLiveTask(editRow.value.id, {
      name: editForm.value.name,
      initial_capital: editForm.value.initial_capital,
      params: editForm.value.params,
    })
    ElMessage.success(t('common.save') + ' ✓')
    editVisible.value = false
    await load()
  } catch (e) { ElMessage.error(t('common.failed') + ': ' + apiErr(e)) }
  finally { saving.value = false }
}

// 盲审A-P2-9：心跳年龄人性化（原裸秒数——stale 86400s 无读性）
const fmtAge = (s) => {
  if (s == null) return '—'
  s = Math.round(s)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m${s % 60 ? ' ' + (s % 60) + 's' : ''}`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h${m % 60 ? ' ' + (m % 60) + 'm' : ''}`
  const d = Math.floor(h / 24)
  return `${d}d${h % 24 ? ' ' + (h % 24) + 'h' : ''}`
}

onMounted(async () => {
  // wd-20 §1.5：enrichTasks 移到 load 之后（原在 tasks 为空时先跑=恒空转）
  const pre = route.query.strategy   // 深链预填(回测页'创建实盘任务')
  if (pre) { dialogVisible.value = true; form.value.strategy_id = String(pre) }
  // 批9：三 loader 互不依赖→并发；尾部 enrichTasks 删（load() 内已调，纯冗余双跑）
  await Promise.all([load(), loadStrategies(), loadAccounts()])
  // 批113：ConnectionCards「历史」深链（query.freeze=<tid>）→ 程序化展开该任务行并拉冻结史。
  // 受控 expand-row-keys 改值不触发 expand-change，故手动 loadFreeze（stopped 行 preloadFreeze 不拉）。
  const freeze = route.query.freeze
  if (freeze != null && freeze !== '') {
    await nextTick()
    const row = tasks.value.find(x => String(x.id) === String(freeze))
    if (row) {
      expanded.value = [row.id]
      if (row._freeze === undefined) loadFreeze(row.id)
    }
  }
})

// wd-20 §1.5 方案 A：自愈时间线（05 §5.8）——幽灵端点 /live-task/{id}/detail 已删
// （wd-19 P0：404 恒吞）。数据源两路：①列表行已有字段（NRestarts 语义近似=心跳龄/冻结/
// md_mode 在列）②按需展开拉 task_logs?task_id= 过滤渲染
const enrichTasks = async () => {
  await Promise.all(tasks.value.map(async task => {
    try {
      const r = await api.get('/log', { params: { task_id: `live:${task.id}` } })
      task._timeline = (r?.logs || []).slice(0, 100)   // 盲审A-P2-8：满窗计数（渲染侧 slice(0,8)）
    } catch (e) { task._timeline = e?.response?.status === 403 ? null : [] }   // 批25：403=无权限(null 与空时间线区分——防重启数假 0)
  }))
}
// wd-20 §1.5 裁定②：重启/退出码由 task_logs 时间线派生（启动条目数-1=重启数）
const restartCount = row => row._timeline === null ? '—' : Math.max((row._timeline || []).filter(l => (l.msg || '').includes('任务启动')).length - 1, 0)
const lastExit = row => {
  const e = (row._timeline || []).find(l => (l.msg || '').includes('退出'))
  return e ? (e.msg.includes('退出码 0') ? '0' : e.msg.slice(0, 24)) : '—'
}

// P1-5(05 §5.8):自愈时间线数据(NRestarts)+快照查看

</script>

<style scoped>
/* 批 76b：进行中冻结行高亮。用 color-mix 派生透明底——不引新 hex（令牌门只许减不许增），
   也不依赖不存在的 --warn-bg（曾误用 ⇒ 静默透明，无任何报错）。 */
.fz-open { background: color-mix(in srgb, var(--warn-fill) 14%, transparent); color: var(--warn); }
.fz-closed { color: inherit; }
</style>
