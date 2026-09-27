<template>
  <!-- wd-20 §2.2：TabsShell 壳（label 走 i18n；query ?tab= 同步） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="sync" v-slot="slotProps">
              <DataManage v-if="slotProps.tab === 'sync'" />
              <DataIntegrity v-else-if="slotProps.tab === 'integrity'" />
              <QualityCheck v-else-if="slotProps.tab === 'quality'" />
              <TaskManager v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import TabsShell from '../components/TabsShell.vue'
import DataManage from './DataManage.vue'
import DataIntegrity from './DataIntegrity.vue'
import QualityCheck from './QualityCheck.vue'
import TaskManager from './TaskManager.vue'
const tabs = [
    { key: 'sync', i18nKey: 'tabs.sync' },
    { key: 'integrity', i18nKey: 'tabs.integrity' },
    { key: 'quality', i18nKey: 'tabs.quality' },
    { key: 'sched', i18nKey: 'tabs.sched' },
  ]
</script>
