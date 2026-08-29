<template>
  <!-- P3-4（web-design 05 §5.10）：数据运维四合一（同步任务/完整性/数据源侧/调度） -->
  <el-card><template #header>
    <el-tabs v-model="tab" @tab-change="v => $router.replace({ query: { ...$route.query, tab: v } })">
      <el-tab-pane name="sync"><template #label><b>同步任务</b></template></el-tab-pane>
      <el-tab-pane name="integrity"><template #label><b>完整性体检</b></template></el-tab-pane>
      <el-tab-pane name="sched"><template #label><b>调度</b></template></el-tab-pane>
    </el-tabs>
  </template>
  <DataManage v-if="tab === 'sync'" />
  <DataIntegrity v-else-if="tab === 'integrity'" />
  <TaskManager v-else />
  </el-card>
</template>
<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import DataManage from './DataManage.vue'
import DataIntegrity from './DataIntegrity.vue'
import TaskManager from './TaskManager.vue'
const route = useRoute()
const tab = ref(route.query.tab || 'sync')
</script>
