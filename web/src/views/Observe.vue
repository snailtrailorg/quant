<template>
  <!-- wd-20 §2.2：TabsShell 壳（label 走 i18n；query ?tab= 同步） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="health" v-slot="slotProps">
              <Health v-if="slotProps.tab === 'health'" />
              <Logs v-else-if="slotProps.tab === 'logs'" />
              <Audit v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import TabsShell from '../components/TabsShell.vue'
import Health from './Health.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
const tabs = [
    { key: 'health', i18nKey: 'tabs.health' },
    { key: 'logs', i18nKey: 'tabs.logs' },
    { key: 'audit', i18nKey: 'tabs.audit' },
  ]
</script>
