<template>
  <!-- 设置五 tab(03 号 §3.3/09 号 A3+wd-20 §2.2 TabsShell；批 7 增告警 tab)。
       批11：原"账号与邀请"迁往 UserManagement.vue（系统管理→用户管理），本 users tab 只剩 API 密钥 -->
  <el-card>
    <template #header>
      <TabsShell :tabs="visibleTabs" default-tab="run" v-slot="slotProps">
        <RunConfig v-if="slotProps.tab === 'run'" />
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
import Permissions from './Permissions.vue'
import AlertSettings from './AlertSettings.vue'
import Profile from './Profile.vue'

const isAdmin = localStorage.getItem('role') === 'admin'
// perm/alerts=admin 门控（页面自验兜底在其内）。批16：users tab（API 密钥）删除——能力在集成中心·交易账户（只读残留）
// ——GET /api/account 需 account_keys 权限,非 admin 点开必 loadFailed（盲审 P2-10,原"账号与邀请"期即存在的错放）
const visibleTabs = computed(() => isAdmin
  ? [
      { key: 'run', i18nKey: 'tabs.run' },
      { key: 'perm', i18nKey: 'tabs.perm' },
      { key: 'alerts', i18nKey: 'tabs.alerts' },
      { key: 'profile', i18nKey: 'tabs.profile' },
    ]
  : [
      { key: 'run', i18nKey: 'tabs.run' },
      { key: 'profile', i18nKey: 'tabs.profile' },
    ])
</script>
