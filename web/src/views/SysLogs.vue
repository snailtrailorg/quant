<template>
  <!-- 批163：系统日志合并页签（原 Observe 顶层 logs/audit 两签收编为子签）。
       嵌套 TabsShell：queryKey=ltab 异名（批11D 嵌套容器纪律——外层 tab 同名互踩盲审 P1 教训）。 -->
  <TabsShell :tabs="tabs" default-tab="logs" query-key="ltab" memory-key="syslogs-subtab" v-slot="slotProps">
    <Logs v-if="slotProps.tab === 'logs'" />
    <Audit v-else-if="slotProps.tab === 'audit'" />
  </TabsShell>
</template>

<script setup>
import TabsShell from '../components/TabsShell.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
const tabs = [
  { key: 'logs', i18nKey: 'tabs.logs' },      // 子签键沿用（零新词）
  { key: 'audit', i18nKey: 'tabs.audit' },
]
</script>
