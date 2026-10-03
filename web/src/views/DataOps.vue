<template>
  <!-- wd-20 §2.2：TabsShell 壳（label 走 i18n；query ?tab= 同步）。
       批 88：**标的资料+路由诊断迁入**（原寄居地=集成中心 security tab / ops 组菜单「数据源优先级」——
       域错位第三例收官：数据域的查询与诊断归数据管理页。权重编辑卡随迁下架，见 RoutingPolicy 头注）。 -->
  <el-card>
    <template #header>
      <TabsShell :tabs="tabs" default-tab="sync" v-slot="slotProps">
              <DataManage v-if="slotProps.tab === 'sync'" />
              <DataIntegrity v-else-if="slotProps.tab === 'integrity'" />
              <QualityCheck v-else-if="slotProps.tab === 'quality'" />
              <TaskManager v-else-if="slotProps.tab === 'sched'" />
              <RoutingPolicy v-else-if="slotProps.tab === 'routing'" />
              <SecurityMaster v-else />
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
import RoutingPolicy from './RoutingPolicy.vue'
import SecurityMaster from './SecurityMaster.vue'
const tabs = [
    { key: 'sync', i18nKey: 'tabs.sync' },
    { key: 'integrity', i18nKey: 'tabs.integrity' },
    { key: 'quality', i18nKey: 'tabs.quality' },
    { key: 'sched', i18nKey: 'tabs.sched' },
    { key: 'routing', i18nKey: 'tabs.routing' },
    { key: 'security', i18nKey: 'tabs.security' },
  ]
</script>
