<template>
  <!-- 批22：系统监控页（原「健康与日志」改名）——三组卡片（指标/服务/连接）+ 底部[通知|日志|审计]页签 -->
  <div>
    <SystemMetricsCards />
    <ServiceStatusCards style="margin-top: var(--sp-4)" />
    <ConnectionCards style="margin-top: var(--sp-4)" />
    <el-card shadow="never" style="margin-top: var(--sp-4)">
      <TabsShell :tabs="tabs" default-tab="notifications" v-slot="slotProps">
        <NotificationTable v-if="slotProps.tab === 'notifications'" />
        <Logs v-else-if="slotProps.tab === 'logs'" />
        <Audit v-else />
      </TabsShell>
    </el-card>
  </div>
</template>

<script setup>
import TabsShell from '../components/TabsShell.vue'
import SystemMetricsCards from '../components/SystemMetricsCards.vue'
import ServiceStatusCards from '../components/ServiceStatusCards.vue'
import ConnectionCards from '../components/ConnectionCards.vue'
import NotificationTable from '../components/NotificationTable.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
const tabs = [
  { key: 'notifications', i18nKey: 'tabs.notifications' },
  { key: 'logs', i18nKey: 'tabs.logs' },
  { key: 'audit', i18nKey: 'tabs.audit' },
]
</script>
