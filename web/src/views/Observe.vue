<template>
  <!-- 批31：系统监控页重组（用户裁定：内容重新组织零功能变更）——顶层三页签：
       运行状态（资源消耗[原系统指标仅改名]+系统服务+实时连接）/ 运行日志（含底部邮件发件箱——批22 hotfix3
       裁定随迁）/ 审计日志。默认落运行状态。组件全部复用零改动，外层"系统日志"卡壳随页签提升拆除。 -->
  <div>
    <TabsShell :tabs="tabs" default-tab="status" v-slot="slotProps">
      <div v-if="slotProps.tab === 'status'">
        <SystemMetricsCards />
        <ServiceStatusCards style="margin-top: var(--sp-4)" />
        <ConnectionCards style="margin-top: var(--sp-4)" />
      </div>
      <Logs v-else-if="slotProps.tab === 'logs'" />
      <Audit v-else />
    </TabsShell>
  </div>
</template>

<script setup>
import TabsShell from '../components/TabsShell.vue'
import SystemMetricsCards from '../components/SystemMetricsCards.vue'
import ServiceStatusCards from '../components/ServiceStatusCards.vue'
import ConnectionCards from '../components/ConnectionCards.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
const tabs = [
  { key: 'status', i18nKey: 'sysmon.runStatus' },   // 批31：页签提升（监控专名进 sysmon 域，共享 tabs 域不染）
  { key: 'logs', i18nKey: 'tabs.logs' },
  { key: 'audit', i18nKey: 'tabs.audit' },
]
</script>
