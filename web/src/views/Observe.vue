<template>
  <!-- P3-5（web-design 05 §5.10）：可观测性三合一（健康/日志/审计） -->
  <el-card><template #header>
    <el-tabs v-model="tab" @tab-change="v => $router.replace({ query: { ...$route.query, tab: v } })">
      <el-tab-pane name="health"><template #label><b>健康</b></template></el-tab-pane>
      <el-tab-pane name="logs"><template #label><b>日志</b></template></el-tab-pane>
      <el-tab-pane name="audit"><template #label><b>审计</b></template></el-tab-pane>
    </el-tabs>
  </template>
  <Health v-if="tab === 'health'" />
  <Logs v-else-if="tab === 'logs'" />
  <Audit v-else />
  </el-card>
</template>
<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import Health from './Health.vue'
import Logs from './Logs.vue'
import Audit from './Audit.vue'
const route = useRoute()
const tab = ref(route.query.tab || 'health')
</script>
