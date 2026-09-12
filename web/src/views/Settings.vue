<template>
  <!-- 设置页(03 号 §3.3/09 号 A3+wd-20 §2.2 TabsShell；批 7 增告警 tab)。
       批11：原"账号与邀请"迁往 UserManagement.vue（系统管理→用户管理）。
       批16：users tab（API 密钥）删除——能力在集成中心·交易账户（只读残留）；
             profile tab 删除——个人中心弹窗唯一入口（头像下拉/深链 ?profile=） -->
  <el-card>
    <template #header>
      <TabsShell :tabs="visibleTabs" default-tab="run" v-slot="slotProps">
        <RunConfig v-if="slotProps.tab === 'run'" />
        <Permissions v-else-if="slotProps.tab === 'perm'" />
        <AlertSettings v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import { computed } from 'vue'
import TabsShell from '../components/TabsShell.vue'
import RunConfig from '../components/RunConfig.vue'
import Permissions from './Permissions.vue'
import AlertSettings from './AlertSettings.vue'

const isAdmin = localStorage.getItem('role') === 'admin'
// perm/alerts=admin 门控（页面自验兜底在其内）
const visibleTabs = computed(() => isAdmin
  ? [
      { key: 'run', i18nKey: 'tabs.run' },
      { key: 'perm', i18nKey: 'tabs.perm' },
      { key: 'alerts', i18nKey: 'tabs.alerts' },
    ]
  : [
      { key: 'run', i18nKey: 'tabs.run' },
    ])
</script>
