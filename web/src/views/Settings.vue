<template>
  <!-- 设置五 tab(03 号 §3.3/09 号 A3+wd-20 §2.2 TabsShell；批 7 增告警 tab) -->
  <el-card>
    <template #header>
      <TabsShell :tabs="visibleTabs" default-tab="run" v-slot="slotProps">
        <RunConfig v-if="slotProps.tab === 'run'" />
        <Account v-else-if="slotProps.tab === 'users'" />
        <Permissions v-else-if="slotProps.tab === 'perm'" />
        <AlertSettings v-else-if="slotProps.tab === 'alerts'" />
        <Profile v-else />
      </TabsShell>
    </template>
  </el-card>
</template>
<script setup>
import { computed } from 'vue'
import TabsShell from '../components/TabsShell.vue'
import RunConfig from '../components/RunConfig.vue'
import Account from './Account.vue'
import Permissions from './Permissions.vue'
import AlertSettings from './AlertSettings.vue'
import Profile from './Profile.vue'

const isAdmin = localStorage.getItem('role') === 'admin'
// perm/alerts=admin 门控（页面自验兜底在其内）
const visibleTabs = computed(() => isAdmin
  ? [
      { key: 'run', i18nKey: 'tabs.run' },
      { key: 'users', i18nKey: 'tabs.users' },
      { key: 'perm', i18nKey: 'tabs.perm' },
      { key: 'alerts', i18nKey: 'tabs.alerts' },
      { key: 'profile', i18nKey: 'tabs.profile' },
    ]
  : [
      { key: 'run', i18nKey: 'tabs.run' },
      { key: 'users', i18nKey: 'tabs.users' },
      { key: 'profile', i18nKey: 'tabs.profile' },
    ])
</script>
