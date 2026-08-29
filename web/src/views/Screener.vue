<template>
  <el-card>
    <template #header>
      <!-- P2-8（web-design 05 §5.9）：选股器三合一——A股/可转债/ETF 同页 tab（URL 记忆 tab 支持） -->
      <el-tabs v-model="tab" @tab-change="onTab">
        <el-tab-pane name="astock"><template #label><b>A股</b></template></el-tab-pane>
        <el-tab-pane name="cb"><template #label><b>可转债</b></template></el-tab-pane>
        <el-tab-pane name="etf"><template #label><b>ETF</b></template></el-tab-pane>
      </el-tabs>
    </template>
    <AScreen v-if="tab === 'astock'" />
    <CBScreen v-else-if="tab === 'cb'" />
    <ETFScreen v-else />
  </el-card>
</template>
<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AScreen from './AScreen.vue'
import CBScreen from './CBScreen.vue'
import ETFScreen from './ETFScreen.vue'

const route = useRoute()
const router = useRouter()
const tab = ref(route.query.tab || 'astock')
const onTab = (v) => router.replace({ query: { ...route.query, tab: v } })   // 05 §5.0-5：tab 写 URL
</script>
