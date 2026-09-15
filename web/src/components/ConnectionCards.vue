<template>
  <!-- 批22：实时连接状态卡片组（hub + 每实盘任务一卡） -->
  <!-- 批24 结构统一：组标题从卡外裸 h4 归位 el-card header（用户管理式——批23 表头规则同构） -->
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>{{ t('sysmon.connections') }}</span></div>
    </template>
    <div class="card-grid">
      <el-card shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">md-hub</span>
          <el-tag :type="hub ? 'success' : 'danger'" size="small" effect="light">
            {{ hub ? t('sysmon.online') : t('sysmon.offline') }}
          </el-tag>
        </div>
        <div v-if="hub" class="card-meta">gen {{ hub.gen }} · subs {{ hub.subs }} · bars {{ hub.bars }}</div>
        <div v-else class="card-meta">{{ t('sysmon.noData') }}</div>
      </el-card>
      <el-card v-for="(tk, tid) in tasks" :key="tid" shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">task-{{ tid }}</span>
          <el-tag :type="tk.frozen ? 'warning' : 'success'" size="small" effect="light">
            {{ tk.frozen ? t('sysmon.frozen') : t('sysmon.running') }}
          </el-tag>
        </div>
        <div class="card-meta">md {{ tk.md }} · lag {{ tk.lag == null ? '—' : Math.round(tk.lag) + 's' }}</div>
      </el-card>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHealthComponents } from '../api'

const { t } = useI18n()
const hub = ref(null)
const tasks = ref({})
let pollTimer = null

const load = async () => {
  try {
    const snap = await getHealthComponents()
    const next = { hub: snap.hub || null, tasks: snap.tasks || {} }
    const sig = JSON.stringify(next)
    if (sig !== lastSig) { lastSig = sig; hub.value = next.hub; tasks.value = next.tasks }   // 数据未变不赋值
  } catch {}
}
let lastSig = ''
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-3); }   /* 每行 3 卡随容器宽等分伸缩 */
.status-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-name { font-size: var(--fs-label); font-weight: 600; }
.card-meta { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); }
</style>
