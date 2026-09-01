<template>
  <!-- 运行配置卡(从 SystemConfig 拆出通用配置;设置·运行配置 tab 用,批 1 归位重组) -->
  <el-card>
    <template #header>{{ t('systemConfig.title') }}</template>
    <el-table :data="configs">
      <el-table-column prop="key" :label="t('common.configKey')" width="200" />
      <el-table-column :label="t('common.configValue')" width="200">
        <template #default="{ row }">
          <el-input-number v-if="row.value_type === 'int' || row.value_type === 'float'"
            v-model="row.editValue" :step="1" style="width: 140px" />
          <el-switch v-else-if="row.value_type === 'bool'" v-model="row.editValue" />
          <el-input v-else-if="row.value_type === 'password'" v-model="row.editValue" type="password" show-password
            style="width: 180px" :placeholder="row.has_value ? t('systemConfig.pwdSet') : t('systemConfig.pwdEmpty')" />
          <el-input v-else v-model="row.editValue" style="width: 180px" />
        </template>
      </el-table-column>
      <el-table-column prop="value_type" :label="t('common.type')" width="80" />
      <el-table-column prop="description" :label="t('risk.label')" show-overflow-tooltip />
      <el-table-column :label="t('common.updatedAt')" width="180">
        <template #default="{ row }">{{ row.updated_at }}</template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="120">
        <template #default="{ row }">
          <el-button type="primary" @click="save(row)" :loading="row._saving">{{ t('common.save') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="color: #999; font-size: 12px; margin-top: 12px">{{ t('systemConfig.hint') }}</div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getSystemConfig, updateSystemConfig, apiErr } from '../api'

const { t } = useI18n()
const configs = ref([])
const load = async () => {
  try {
    const r = await getSystemConfig()
    configs.value = (r.items || []).filter(c => !c.key.startsWith('smtp_')).map(c => {
      let editValue = c.value
      if (c.value_type === 'int') editValue = parseInt(c.value)
      else if (c.value_type === 'float') editValue = parseFloat(c.value)
      else if (c.value_type === 'bool') editValue = c.value === 'true'
      return { ...c, editValue, _saving: false }
    })
  } catch (e) { ElMessage.error(t('common.loadFailed')) }
}
const save = async (row) => {
  row._saving = true
  try {
    const res = await updateSystemConfig(row.key, row.editValue)
    if (res.dynamic) {
      const d = res.dynamic
      if (d.applied) {
        ElMessage.success(t('systemConfig.updatedDynamic', { workers: JSON.stringify(d.workers) }))
      } else {
        ElMessage.warning(t('systemConfig.updatedReason', { reason: d.reason }))
      }
    } else {
      ElMessage.success(t('systemConfig.updated'))
    }
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { row._saving = false }
}
onMounted(load)
</script>
