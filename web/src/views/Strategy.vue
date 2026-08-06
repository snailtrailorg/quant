<template>
  <el-card>
    <template #header>{{ t('strategy.title') }}</template>
    <el-table :data="strategies" stripe>
      <el-table-column prop="name" :label="t('strategy.name')" />
      <el-table-column prop="type" :label="t('strategy.type')" />
      <el-table-column prop="symbol" :label="t('strategy.symbol')" />
      <el-table-column :label="t('strategy.status')">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '运行中' : '已停' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button size="small" type="success" @click="onStart(row.id)" v-if="!row.enabled">{{ t('strategy.start') }}</el-button>
          <el-button size="small" type="danger" @click="onStop(row.id)" v-if="row.enabled">{{ t('strategy.stop') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getStrategies, startStrategy, stopStrategy } from '../api'

const { t } = useI18n()
const strategies = ref([])
const load = async () => { strategies.value = await getStrategies() }
const onStart = async id => { await startStrategy(id); ElMessage.success('已启动'); load() }
const onStop = async id => { await stopStrategy(id); ElMessage.success('已停止'); load() }
onMounted(load)
</script>
