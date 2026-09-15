<template>
  <!-- 批22：系统监控页——三组卡片（指标/服务/连接）+ 底部三页签（迭代十六 hotfix3 用户裁定：邮件发件箱并回运行日志页签底部，
       不独立成签；卡片 header 标题「系统日志」与上三 section 同构） -->
  <div>
    <SystemMetricsCards />
    <ServiceStatusCards style="margin-top: var(--sp-4)" />
    <ConnectionCards style="margin-top: var(--sp-4)" />
    <el-card shadow="never" style="margin-top: var(--sp-4)">
      <template #header>
        <span>{{ t('sysmon.logsTitle') }}</span>
      </template>
      <!-- 批28-3（用户裁定：已决策干掉）：通知消息页签退役——NotificationTable 删除，仅留双页签 -->
      <TabsShell :tabs="tabs" default-tab="logs" v-slot="slotProps">
        <Logs v-if="slotProps.tab === 'logs'" />
        <Audit v-else />
      </TabsShell>
    </el-card>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import TabsShell from '../components/TabsShell.vue'
import SystemMetricsCards from '../components/SystemMetricsCards.vue'
import ServiceStatusCards from '../components/ServiceStatusCards.vue'
import ConnectionCards from '../components/ConnectionCards.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
const tabs = [
  { key: 'logs', i18nKey: 'tabs.logs' },
  { key: 'audit', i18nKey: 'tabs.audit' },
]
const { t } = useI18n()   // 迭代十六 hotfix：header 标题词条——重写时漏解构致 observe 崩+组件树连锁全白
</script>
