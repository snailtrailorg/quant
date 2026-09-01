<template>
  <el-row :gutter="20">
    <el-col :span="12">
      <!-- P1-7（09-A8）：md-hub 每日连接窗状态 + 影子双轨灰度进度（08-28 批6a 已切换 hub 模式,影子对照终结） -->
    <el-alert type="info" :closable="false" style="margin-bottom: 14px">
      {{ t('health.hubWindow') }} · {{ t('health.shadowDone') }}
    </el-alert>

    <el-card>
        <template #header>{{ t('health.apiHealth') }}</template>
        <el-table :data="healthData">
          <el-table-column prop="name" :label="t('health.service')" min-width="100" show-overflow-tooltip />
          <el-table-column :label="t('common.status')" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" :label="t('common.detail')" show-overflow-tooltip />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>{{ t('health.disk') }}</template>
        <el-table :data="diskData">
          <el-table-column prop="path" :label="t('health.path')" width="150" />
          <el-table-column prop="used" :label="t('health.used')" width="120" />
          <el-table-column prop="total" :label="t('health.total')" width="120" />
          <el-table-column :label="t('health.usage')" width="100">
            <template #default="{ row }">
              <el-progress :percentage="row.pct" :color="row.pct > 85 ? '#f56c6c' : '#67c23a'" :stroke-width="10" />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <!-- 15 号 SM2：组件矩阵（systemd unit / 依赖 / hub / 任务心跳，与 /metrics 同源） -->
  <el-card style="margin-top: 20px">
    <template #header>
      {{ t('health.components') }}
      <span v-if="hub" class="hub-meta">
        hub gen={{ hub.gen }} · {{ t('health.subs') }} {{ hub.subs }} · ticks {{ hub.ticks }}
        <el-tag v-if="hub.tick_age != null" :type="hub.tick_age < 90 ? 'success' : 'warning'" size="small">
          tick {{ Math.round(hub.tick_age) }}s
        </el-tag>
      </span>
    </template>
    <el-table :data="componentRows">
      <el-table-column prop="component" :label="t('health.component')" min-width="200" />
      <el-table-column prop="kind" :label="t('health.kind')" width="110" />
      <el-table-column :label="t('common.status')" width="110">
        <template #default="{ row }">
          <el-tag :type="row.ok ? 'success' : (row.unknown ? 'info' : 'danger')">
            {{ row.state || (row.ok ? 'ok' : (row.unknown ? '?' : 'fail')) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="detail" :label="t('common.detail')" min-width="260" show-overflow-tooltip />
    </el-table>
  </el-card>

  <!-- 15 号 SM2：健康事件流（触发/恢复沿历史） -->
  <el-card style="margin-top: 20px">
    <template #header>{{ t('health.events') }}</template>
    <el-table :data="eventRows">
      <el-table-column prop="ts" :label="t('health.time')" width="170" />
      <el-table-column :label="t('health.severity')" width="100">
        <template #default="{ row }">
          <el-tag :type="sevType(row.severity)" size="small">{{ row.severity }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="component" :label="t('health.component')" min-width="180" />
      <el-table-column prop="rule" :label="t('health.rule')" width="150" />
      <el-table-column prop="detail" :label="t('common.detail')" min-width="280" show-overflow-tooltip />
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHealth, getHealthComponents, getHealthEvents } from '../api'

const { t } = useI18n()
const healthData = ref([])
const diskData = ref([])
const snap = ref(null)
const eventRows = ref([])
const hub = computed(() => snap.value?.hub || null)

const componentRows = computed(() => {
  if (!snap.value) return []
  const rows = []
  for (const [unit, st] of Object.entries(snap.value.units || {})) {
    rows.push({ component: unit, kind: 'systemd', ok: st.ActiveState === 'active',
                state: st.ActiveState, detail: `${st.SubState || ''} restarts=${st.NRestarts ?? '-'}`.trim() })
  }
  for (const [dep, ok] of Object.entries(snap.value.deps || {})) {
    if (typeof ok === 'boolean')
      rows.push({ component: dep, kind: 'dep', ok, state: ok ? 'ok' : 'fail',
                  detail: ok ? '' : String(snap.value.deps[`${dep}_err`] || '') })
  }
  if (snap.value.hub) {
    const h = snap.value.hub
    rows.push({ component: 'md-hub', kind: 'heartbeat', ok: true, state: 'alive',
                detail: `gen=${h.gen} subs=${h.subs} sess_ticks=${h.sess_ticks} bars=${h.bars} dropped_pg=${h.dropped_pg}` })
  } else {
    rows.push({ component: 'md-hub', kind: 'heartbeat', ok: false, state: 'lost',
                detail: t('health.hbLost') })
  }
  for (const [tid, tk] of Object.entries(snap.value.tasks || {})) {
    rows.push({ component: `task-${tid}`, kind: `worker(${tk.md})`, ok: !tk.frozen,
                state: tk.frozen ? 'frozen' : 'alive',
                detail: `bars=${tk.bars} lag=${tk.lag != null ? Math.round(tk.lag) + 's' : '-'}` })
  }
  return rows
})

const sevType = s => s === 'critical' ? 'danger' : s === 'recovery' ? 'success' : 'warning'

onMounted(async () => {
  try {
    const r = await getHealth()
    if (r.results) {
      healthData.value = Object.entries(r.results).map(([k, v]) => ({
        name: k, status: v.status, detail: v.model || v.msg || '',
      }))
    }
    if (r.stats) {
      diskData.value = r.stats.filter(s => s.pct).map(s => ({
        path: s.path, used: `${s.used_gb}GB`, total: `${s.total_gb}GB`, pct: s.pct,
      }))
    }
  } catch { healthData.value = [{ name: '-', status: 'error', detail: t('common.loadFailed') }] }
  try { snap.value = await getHealthComponents() } catch { /* 组件矩阵加载失败不阻塞页面 */ }
  try {
    const r = await getHealthEvents()
    eventRows.value = r.events || []
  } catch { /* 事件流加载失败不阻塞页面 */ }
})

// P3-5(05 §5.10):健康页 30s 自动轮询+手动刷新
import { onUnmounted } from 'vue'
let healthTimer = null
onMounted(() => { healthTimer = setInterval(load, 30000) })
onUnmounted(() => clearInterval(healthTimer))

// P3-5(05 §5.10):健康页 30s 自动轮询+手动刷新

</script>
<style scoped>
.hub-meta { margin-left: 12px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>

