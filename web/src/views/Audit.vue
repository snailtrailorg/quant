<template>
  <el-card>
    <template #header>{{ t('audit.title') }}</template>
    <el-form :inline="true" style="margin-bottom: 12px">
      <el-form-item :label="t('audit.actor')">
        <el-input v-model="filterActor" :placeholder="t('audit.phActor')" size="small" clearable style="width:140px" />
      </el-form-item>
      <el-form-item :label="t('common.action')">
        <el-input v-model="filterAction" :placeholder="t('audit.phAction')" size="small" clearable style="width:160px" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" size="small" @click="load">{{ t('common.filter') }}</el-button>
        <el-button size="small" @click="filterActor='';filterAction='';load()">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
    <el-table :data="filteredLogs" stripe>
      <el-table-column prop="ts" :label="t('common.time')" width="200">
        <template #default="{ row }">{{ row.ts.replace('T', ' ').slice(0, 19) }}</template>
      </el-table-column>
      <el-table-column prop="actor" :label="t('audit.actor')" width="120" />
      <el-table-column prop="action" :label="t('common.action')" width="150" />
      <el-table-column prop="target" :label="t('audit.target')" width="150" />
      <el-table-column prop="detail" :label="t('common.detail')" />
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getAudit } from '../api'
const { t } = useI18n()
const logs = ref([])
const filterActor = ref('')
const filterAction = ref('')
const filteredLogs = computed(() => {
  let r = logs.value
  if (filterActor.value) r = r.filter(l => l.actor?.includes(filterActor.value))
  if (filterAction.value) r = r.filter(l => l.action?.includes(filterAction.value))
  return r
})
onMounted(async () => { try { logs.value = await getAudit() } catch (e) { console.error(e) } })
</script>
