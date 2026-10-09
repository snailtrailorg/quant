<template>
  <!-- 批22：实时连接状态卡片组（hub + 每实盘任务一卡） -->
  <!-- 批24 结构统一：组标题从卡外裸 h4 归位 el-card header（用户管理式——批23 表头规则同构） -->
  <!-- 批66b（D26）：hub N 实例（账号级 per-account）——每 hub 一卡，与 tasks 同构 v-for -->
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>{{ t('sysmon.connections') }}</span></div>
    </template>
    <div class="card-grid">
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
let pollTimer = null

// 批113：task 卡片「历史」→ LiveTask 展开行冻结史深链（query.freeze=<tid>，LiveTask 读它自动展开该行）
const goHistory = (tid) => router.push({ name: 'live-task', query: { freeze: tid } })

const load = async () => {
  try {
    const snap = await getHealthComponents()
    const next = { hubs: snap.hubs || {}, tasks: snap.tasks || {} }
    const sig = JSON.stringify(next)
    if (sig !== lastSig) { lastSig = sig; hubs.value = next.hubs; tasks.value = next.tasks }   // 数据未变不赋值
  } catch {}
}
let lastSig = ''
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--sp-3); }   /* 每行 4 卡（批78 用户裁定：三卡组统一）随容器宽等分伸缩 */
.status-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-name { font-size: var(--fs-label); font-weight: 600; }
.card-meta { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); }
.card-hist { margin-top: var(--sp-1); font-size: var(--fs-foot); }
</style>
