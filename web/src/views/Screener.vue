<template>
  <!-- wd-20 §2.2：TabsShell 壳（label 走 i18n；query ?tab= 同步） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="astock" v-slot="slotProps">
              <AScreen v-if="slotProps.tab === 'astock'" />
              <CBScreen v-else-if="slotProps.tab === 'cb'" />
              <ETFScreen v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import TabsShell from '../components/TabsShell.vue'
import AScreen from './AScreen.vue'
import CBScreen from './CBScreen.vue'
import ETFScreen from './ETFScreen.vue'
const tabs = [
    { key: 'astock', i18nKey: 'tabs.astock' },
    { key: 'cb', i18nKey: 'tabs.cb' },
    { key: 'etf', i18nKey: 'tabs.etf' },
  ]
</script>
