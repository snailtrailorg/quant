<template>
  <!-- 批22 追加裁定：通知页签改用「通知历史」表格形态（原 Logs.vue 右列表抽组件——
       与旧 NotificationList 列表形态二选一，用户选表格：dispatch chips + 列显隐 + 全部确认） -->
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--sp-3)">
      <el-button v-if="unacked" size="small" type="primary" @click="onAckAll">{{ t('notify.ackAll') }} ({{ unacked }})</el-button>
      <span v-else></span>
      <ColumnSettings storage-key="cols.sysmon-notify" :columns="colDefs" v-model:visible="visible" />
    </div>
    <TableShell :data="notifs" height="500" storage-key="sysmon-notify">
      <el-table-column v-if="colOn('level')" prop="level" :label="t('log.level')" min-width="100">
        <template #default="{ row }">
          <span :class="['ndot', row.level]"></span>{{ row.level }}
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('category')" prop="category" :label="t('log.notifyCategory')" min-width="100" />
      <el-table-column prop="title" :label="t('log.titleCol')" min-width="200" show-overflow-tooltip />
      <el-table-column v-if="colOn('body')" prop="body" :label="t('log.content')" min-width="220" show-overflow-tooltip />
      <el-table-column v-if="colOn('dispatch')" prop="dispatch" :label="t('alerts.dispatchCol')" min-width="140">
        <template #default="{ row }">
          <template v-if="row.level === 'info'"></template>
          <span v-else-if="!row.dispatch" style="color: var(--flat)">?</span>
          <template v-else>
            <el-tag v-for="(v, ch) in row.dispatch" :key="ch" size="small" style="margin: 1px"
                    :type="chipType(v)" :title="chipTitle(ch, v)">{{ chipLabel(ch, v) }}</el-tag>
          </template>
        </template>
      </el-table-column>
      <el-table-column v-if="colOn('created_at')" prop="created_at" :label="t('common.time')" min-width="160" />
      <el-table-column v-if="colOn('acked_at')" prop="acked_at" :label="t('cols.confirmedAt')" min-width="160">
        <template #default="{ row }">{{ row.acked_at ? fmtTime.full(row.acked_at) : '-' }}</template>
      </el-table-column>
    </TableShell>
  </div>
</template>

<script setup>
import TableShell from './TableShell.vue'
import ColumnSettings from './ColumnSettings.vue'
import { fmtTime } from '../utils/fmtTime'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getNotifications, ackAllNotifications } from '../api'

const { t } = useI18n()
const notifs = ref([])
let pollTimer = null

// 列显示配置（承自 Logs.vue 通知历史表：dispatch chips 列默认隐）
const colDefs = computed(() => [
  { key: 'level', label: t('log.level') },
  { key: 'category', label: t('log.notifyCategory') },
  { key: 'body', label: t('log.content') },
  { key: 'dispatch', label: t('alerts.dispatchCol'), hidden: true },
  { key: 'created_at', label: t('common.time') },
  { key: 'acked_at', label: t('cols.confirmedAt') },
])
const visible = ref([])
const colOn = k => visible.value.includes(k)
const unacked = computed(() => notifs.value.filter(n => !n.acked_at).length)

// 批 7 推送结果 chips（dispatch jsonb 渲染契约：ok✓/queued○/skip|failed 带因/{}不显示/null=未明）
const chipType = v => v === 'ok' || v === 'legacy' ? 'success' : (v === 'queued' || v === 'sending') ? 'info'
  : v.startsWith('failed:') ? 'danger' : v.startsWith('skip:') ? 'warning' : 'info'
const chipLabel = (ch, v) => {
  const base = ch.split(':')[0]   // 批7.1 行级 dkey（email:12）剥后缀取通道名
  const n = ch.includes(':') ? `·${ch.split(':')[1]}` : ''
  const tag = { im: 'IM', email: t('alerts.channel.email'), sms: t('alerts.channel.sms'),
                legacy: 'web', _chain: 'Ⓒ' }[base] || ch
  return (v === 'ok' ? `${tag}✓` : (v === 'queued' || v === 'sending') ? `${tag}○` : v.startsWith('failed:') ? `${tag}✗` : `${tag}–`) + n
}
const chipTitle = (ch, v) => {
  if (v === 'ok') return t('alerts.dispatch.ok')
  if (v === 'queued') return t('alerts.dispatch.queued')
  if (v === 'sending') return t('alerts.dispatch.sending')
  const reason = v.includes(':') ? v.split(':').slice(1).join(':') : ''
  const key = `alerts.dispatch.${reason}`
  const zh = t(key)
  return zh !== key ? `${v.startsWith('skip:') ? t('alerts.dispatch.skip') : t('alerts.dispatch.failed')} · ${zh}` : v
}

const load = async () => {
  try { notifs.value = (await getNotifications('all', 50)).items || [] } catch {}
}
const onAckAll = async () => { try { await ackAllNotifications(); await load() } catch {} }
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.ndot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.ndot.critical { background: var(--critical); }
.ndot.warn { background: var(--warn-fill); }
.ndot.info { background: var(--text-secondary); }
</style>
