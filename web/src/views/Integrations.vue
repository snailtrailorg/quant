<template>
  <!-- P3-3（web-design 05 §5.10）：集成中心六合一（数据源/券商/推送/IM/LLM/SMTP）——tab 壳归并,原组件复用 -->
  <el-card><template #header>
    <el-tabs v-model="tab" @tab-change="v => $router.replace({ query: { ...$route.query, tab: v } })">
      <el-tab-pane v-for="t in tabs" :key="t.k" :name="t.k"><template #label><b>{{ t.label }}</b></template></el-tab-pane>
    </el-tabs>
  </template>
  <component :is="current" />
  </el-card>
</template>
<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import DataSources from './DataSources.vue'
import Brokers from './Brokers.vue'
import Channels from './Channels.vue'
import ImBots from './ImBots.vue'
import LLMModels from './LLMModels.vue'
import SmtpCard from '../components/SmtpCard.vue'
import TradingAccounts from './TradingAccounts.vue'
const { t } = useI18n()
const route = useRoute()
const tabs = [
  { k: 'brokers', label: '券商', c: Brokers },
  { k: 'push', label: '推送通道', c: Channels },
  { k: 'im', label: 'IM 机器人', c: ImBots },
  { k: 'mail', label: '邮件 SMTP', c: SmtpCard },
  { k: 'llm', label: 'LLM 模型', c: LLMModels },
  { k: 'sources', label: '数据源', c: DataSources },
  { k: 'trading', label: '交易账户', c: TradingAccounts },
]
const tab = ref(route.query.tab || 'brokers')
const current = computed(() => (tabs.find(x => x.k === tab.value) || tabs[0]).c)
</script>
