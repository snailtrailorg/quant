<template>
  <!-- 批22：实时连接状态卡片组（hub + 每实盘任务一卡） -->
  <!-- 批24 结构统一：组标题从卡外裸 h4 归位 el-card header（用户管理式——批23 表头规则同构） -->
  <!-- 批66b（D26）：hub N 实例（账号级 per-account）——每 hub 一卡，与 tasks 同构 v-for -->
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>{{ t('sysmon.connections') }}</span></div>
    </template>
    <div class="card-grid" v-loading="firstLoading">
      <el-card v-for="(hb, acct) in hubs" :key="acct" shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">md-hub·{{ acct }}</span>
          <el-tag type="success" size="small" effect="light">{{ t('sysmon.online') }}</el-tag>
        </div>
        <div class="card-meta">gen {{ hb.gen }} · subs {{ hb.subs }} · bars {{ hb.bars }}</div>
      </el-card>
      <el-card v-if="!Object.keys(hubs).length" shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">md-hub</span>
          <el-tag type="danger" size="small" effect="light">{{ t('sysmon.offline') }}</el-tag>
        </div>
        <div class="card-meta">{{ t('sysmon.noData') }}</div>
      </el-card>
      <!-- 批188：TD 执行进程（账号级常驻 td_exec）——expected ∪ alive，每账号一卡 -->
      <el-card v-for="te in tdExecs" :key="'td' + te.id" class="status-card">
        <div class="card-head">
          <span class="card-name">td-exec·{{ te.id }}</span>
          <el-tag :type="te.tagType" size="small" effect="light">
            {{ te.alive ? t('sysmon.online') : t('sysmon.offline') }}
          </el-tag>
        </div>
        <div class="card-meta">{{ te.provider || '—' }} · quote {{ te.quote || '—' }}</div>
      </el-card>
      <el-card v-for="(tk, tid) in tasks" :key="tid" shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">task-{{ tid }}</span>
          <el-tag :type="tk.frozen ? 'warning' : 'success'" size="small" effect="light">
            {{ tk.frozen ? t('sysmon.frozen') : t('sysmon.running') }}
          </el-tag>
        </div>
        <div class="card-meta">md {{ tk.md }} · lag {{ tk.lag == null ? '—' : Math.round(tk.lag) + 's' }}</div>
        <!-- 批113：冻结是 30s 瞬时灯（短冻结会整跳）→「历史」入口接通 F1 持久账（LiveTask 展开行） -->
        <div class="card-hist">
          <el-link type="primary" :underline="false" @click="goHistory(tid)">{{ t('sysmon.history') }}</el-link>
        </div>
      </el-card>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getHealthComponents } from '../api'

const { t } = useI18n()
const router = useRouter()
const hubs = ref({})   // 批66b：{account_id: {gen, subs, bars, ...}}（后端 snap.hubs；legacy account=0）
const tasks = ref({})
const tdExecs = ref([])   // 批188：[{id, alive, provider, quote}]（snap.td_execs expected ∪ alive）
let pollTimer = null

// 批113：task 卡片「历史」→ LiveTask 展开行冻结史深链（query.freeze=<tid>，LiveTask 读它自动展开该行）
const goHistory = (tid) => router.push({ name: 'live-task', query: { freeze: tid } })

const firstLoading = ref(true)   // 批126o：首载转圈（与其他异步页反馈一致）
const load = async () => {
  try {
    const snap = await getHealthComponents()
    const te = snap.td_execs || {}
    const _alive = te.alive || []
    const _det = te.details || {}
    const _ids = Array.from(new Set([...(te.expected || []), ..._alive])).sort((a, b) => a - b)
    const next = {
      hubs: snap.hubs || {}, tasks: snap.tasks || {},
      tdExecs: _ids.map((id) => ({ id, alive: _alive.includes(id),
                                   tagType: _alive.includes(id) ? 'success' : 'danger',
                                   provider: (_det[id] || {}).provider || '',
                                   quote: (_det[id] || {}).quote_account_id || '' })),
    }
    const sig = JSON.stringify(next)
    if (sig !== lastSig) {   // 数据未变不赋值
      lastSig = sig; hubs.value = next.hubs; tasks.value = next.tasks; tdExecs.value = next.tdExecs
    }
  } catch {}
  finally { firstLoading.value = false }
}
let lastSig = ''
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-3); min-height: var(--sp-8); }   /* 每行 4 卡（批78 用户裁定：三卡组统一）随容器宽等分伸缩；min-height 供首载 v-loading 可见（批126o） */
.status-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-name { font-size: var(--fs-label); font-weight: 600; }
.card-meta { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); }
.card-hist { margin-top: var(--sp-1); font-size: var(--fs-foot); }
</style>
