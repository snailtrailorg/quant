<template>
  <!-- wd-20 §2.2：TabsShell 壳（集成中心七合一，动态组件；label 走 i18n 消灭中文字面量） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="mail" v-slot="slotProps">
        <!-- 批39 A-P1-2：空 tabs（无任一页签权限/meOnce 失败）渲染空态——原 tabs[0].c 抛 TypeError 白屏 -->
        <EmptyState v-if="!tabs.length" :description="t('common.noPerm')" />
        <component :is="(tabs.find(x => x.key === slotProps.tab) || tabs[0])?.c" v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'   // 批46 快审 B：批39 空态用 t('common.noPerm') 但无解构——tabs 空时必崩 _ctx.t TypeError（白屏恰踩批39 修复目标）
import { meOnce } from '../api'
import TabsShell from '../components/TabsShell.vue'
import EmptyState from '../components/EmptyState.vue'
import DataSources from './DataSources.vue'
import LLMModels from './LLMModels.vue'
import SmtpCard from '../components/SmtpCard.vue'
import SmsCard from '../components/SmsCard.vue'
import TradingAccounts from './TradingAccounts.vue'
// 批38：tab 级权限门——每 tab 按后端写端点权限键过滤（审批37 快审 P2-2；后端 fail-closed 兜底不变）
const ALL_TABS = [
    // 批38 用户裁定：券商/推送通道页签删除（券商=交易账户重复；推送已在告警通道用户维度实现）
    { key: 'mail', i18nKey: 'tabs.mail', c: SmtpCard, perm: 'user_mgmt' },
    { key: 'sms', i18nKey: 'tabs.sms', c: SmsCard, perm: 'alerts_config' },   // 批37 迁入
    { key: 'llm', i18nKey: 'tabs.llm', c: LLMModels, perm: 'llm_config' },
    { key: 'sources', i18nKey: 'tabs.sources', c: DataSources, perm: 'system_config' },
    { key: 'trading', i18nKey: 'tabs.trading', c: TradingAccounts, perm: 'account_keys' },
]
const _perms = ref([])
const tabs = computed(() => ALL_TABS.filter(x => _perms.value.includes(x.perm)))
const { t } = useI18n()   // 批46 快审 B：空态文案消费（批39 引入时漏）
onMounted(async () => {
  try { _perms.value = (await meOnce())?.permissions || [] } catch { _perms.value = [] }
})
</script>
