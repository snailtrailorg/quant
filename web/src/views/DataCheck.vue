<template>
  <!-- 批166：数据检查（完整性与质量合并——用户裁定页签形态非并排卡） -->
  <TabsShell :tabs="tabs" default-tab="integrity" memory-key="datacheck.tab" v-slot="sp">
    <DataIntegrity v-if="sp.tab === 'integrity'" />
    <QualityCheck v-else />
  </TabsShell>
</template>

<script setup>
import TabsShell from '../components/TabsShell.vue'
import DataIntegrity from './DataIntegrity.vue'
import QualityCheck from './QualityCheck.vue'
const tabs = [
  { key: 'integrity', i18nKey: 'dataManage.integrity' },
  { key: 'quality', i18nKey: 'dataManage.quality' },
]
</script>
