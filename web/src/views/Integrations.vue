<template>
  <!-- wd-20 §2.2：TabsShell 壳（集成中心七合一，动态组件；label 走 i18n 消灭中文字面量） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="brokers" v-slot="slotProps">
        <component :is="(tabs.find(x => x.key === slotProps.tab) || tabs[0]).c" />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import TabsShell from '../components/TabsShell.vue'
import DataSources from './DataSources.vue'
import Brokers from './Brokers.vue'
import Channels from './Channels.vue'
import ImBots from './ImBots.vue'
import LLMModels from './LLMModels.vue'
import SmtpCard from '../components/SmtpCard.vue'
import TradingAccounts from './TradingAccounts.vue'
const tabs = [
    { key: 'brokers', i18nKey: 'tabs.brokers', c: Brokers },
    { key: 'push', i18nKey: 'tabs.push', c: Channels },
    { key: 'im', i18nKey: 'tabs.im', c: ImBots },
    { key: 'mail', i18nKey: 'tabs.mail', c: SmtpCard },
    { key: 'llm', i18nKey: 'tabs.llm', c: LLMModels },
    { key: 'sources', i18nKey: 'tabs.sources', c: DataSources },
    { key: 'trading', i18nKey: 'tabs.trading', c: TradingAccounts },
]
</script>
