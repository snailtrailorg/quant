<template>
  <!-- 设置四 tab(03 号 §3.3/09 号 A3:运行配置|账号与邀请|权限管理|个人资料;批 1 归位重组 2026-08-30) -->
  <el-card>
    <template #header>
      <el-tabs v-model="tab" @tab-change="v => $router.replace({ query: { ...$route.query, tab: v } })">
        <el-tab-pane name="run"><template #label><b>{{ t('settings.run') }}</b></template></el-tab-pane>
        <el-tab-pane name="users"><template #label><b>{{ t('settings.users') }}</b></template></el-tab-pane>
        <el-tab-pane v-if="isAdmin" name="perm"><template #label><b>{{ t('settings.perm') }}</b></template></el-tab-pane>
        <el-tab-pane name="profile"><template #label><b>{{ t('settings.profile') }}</b></template></el-tab-pane>
      </el-tabs>
    </template>
    <RunConfig v-if="tab === 'run'" />
    <Account v-else-if="tab === 'users'" />
    <Permissions v-else-if="tab === 'perm'" />
    <Profile v-else />
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import RunConfig from '../components/RunConfig.vue'
import Account from './Account.vue'
import Permissions from './Permissions.vue'
import Profile from './Profile.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const isAdmin = localStorage.getItem('role') === 'admin'
const tab = ref(route.query.tab || 'run')
</script>
