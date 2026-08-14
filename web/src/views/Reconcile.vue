<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('reconcile.title') }}</span>
        <el-button @click="load" size="small">{{ t('common.refresh') }}</el-button>
      </div>
    </template>
    <el-alert :title="summary" :type="hasIssues ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />
    <el-table :data="issues" stripe>
      <el-table-column prop="type" :label="t('reconcile.issueType')" width="200" />
      <el-table-column prop="count" :label="t('reconcile.count')" width="80" />
      <el-table-column prop="detail" :label="t('common.detail')" />
    </el-table>
    <el-alert v-if="!issues.length" type="success" :title="t('reconcile.noIssue')" :closable="false" style="margin-top: 20px" />
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getReconcile } from '../api'

const { t } = useI18n()
const issues = ref([])
const hasIssues = computed(() => issues.value.length > 0)
const summary = computed(() => hasIssues.value ? t('reconcile.foundIssues', { n: issues.value.length }) : t('reconcile.allConsistent'))

const load = async () => {
  try {
    const r = await getReconcile()
    issues.value = (r.issues || []).map(issue => ({ type: t('reconcile.issueLabel'), count: 1, detail: issue }))
  } catch (e) { ElMessage.error(t('reconcile.queryFailed')) }
}
onMounted(load)
</script>
