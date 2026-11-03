<template>
  <ContentCard :title="t('screener.plans')">
    <template #actions>
      <IconBtn size="small" :icon="Plus" :title="t('screener.newPlan')" @click="emit('create')" />
    </template>
    <TableShell :data="plans" fill :loading="loading" storage-key="screener-plans"
                highlight-current-row @current-change="onRowClick">
      <el-table-column prop="name" :label="t('screener.planName')" min-width="90" show-overflow-tooltip />
      <el-table-column :label="t('common.action')" width="112">
        <template #default="{ row }">
          <IconBtn size="small" :icon="CaretRight" :title="t('screener.applyPlan')" @click.stop="emit('apply', row)" />
          <IconBtn size="small" :icon="Edit" :title="t('screener.editPlan')" @click.stop="emit('edit', row)" />
          <IconBtn size="small" :icon="Delete" :title="t('screener.delPlan')" @click.stop="onDel(row)" />
        </template>
      </el-table-column>
    </TableShell>
    <div v-if="!loading && !plans.length" class="empty">{{ t('screener.noPlans') }}</div>
  </ContentCard>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'
import ContentCard from './ContentCard.vue'
import TableShell from './TableShell.vue'
import IconBtn from './IconBtn.vue'
import { Plus, Edit, Delete, CaretRight } from '@element-plus/icons-vue'

const props = defineProps({ plans: { type: Array, default: () => [] }, loading: Boolean, activeId: { type: Number, default: null } })
const emit = defineEmits(['create', 'edit', 'apply', 'changed'])
const { t } = useI18n()

const onRowClick = (row) => { if (row) emit('apply', row) }
const onDel = async (row) => {
  try {
    await ElMessageBox.confirm(t('screener.delPlanConfirm', { name: row.name }), t('screener.delPlan'), { type: 'warning' })
  } catch { return }
  try {
    await api.delete(`/screen/presets/${row.id}`)
    emit('changed')
  } catch (e) { ElMessage.error(apiErr(e, t('common.failed'))) }
}
</script>

<style scoped>
.empty { font-size: var(--fs-foot); color: var(--text-secondary); padding: var(--sp-2) 0; text-align: center; }
</style>
