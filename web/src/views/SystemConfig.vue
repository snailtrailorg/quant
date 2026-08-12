<template>
  <el-card>
    <template #header>
      <span>系统配置</span>
    </template>
    <el-table :data="configs" stripe>
      <el-table-column prop="key" label="配置项" width="200" />
      <el-table-column label="值" width="180">
        <template #default="{ row }">
          <el-input-number v-if="row.value_type === 'int' || row.value_type === 'float'"
            v-model="row.editValue" :step="1" size="small" style="width: 140px" />
          <el-switch v-else-if="row.value_type === 'bool'" v-model="row.editValue" />
          <el-input v-else v-model="row.editValue" size="small" style="width: 160px" />
        </template>
      </el-table-column>
      <el-table-column prop="value_type" label="类型" width="80" />
      <el-table-column prop="description" label="说明" />
      <el-table-column label="更新时间" width="180">
        <template #default="{ row }">{{ row.updated_at }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="save(row)" :loading="row._saving">保存</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="color: #999; font-size: 12px; margin-top: 12px">
      ⚙️ celery_concurrency 改后即时生效（Celery worker 在线时 pool_grow/shrink），其他配置项可能需重启服务
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getSystemConfig, updateSystemConfig } from '../api'

const configs = ref([])

const load = async () => {
  try {
    const r = await getSystemConfig()
    configs.value = (r.items || []).map(c => {
      let editValue = c.value
      if (c.value_type === 'int') editValue = parseInt(c.value)
      else if (c.value_type === 'float') editValue = parseFloat(c.value)
      else if (c.value_type === 'bool') editValue = c.value === 'true'
      return { ...c, editValue, _saving: false }
    })
  } catch (e) { ElMessage.error('加载失败') }
}

const save = async (row) => {
  row._saving = true
  try {
    const res = await updateSystemConfig(row.key, row.editValue)
    if (res.dynamic) {
      const d = res.dynamic
      if (d.applied) {
        ElMessage.success(`已更新并动态生效: ${JSON.stringify(d.workers)}`)
      } else {
        ElMessage.warning(`已更新（${d.reason}）`)
      }
    } else {
      ElMessage.success('已更新')
    }
    await load()
  } catch (e) { ElMessage.error('保存失败: ' + (e?.error || '')) }
  finally { row._saving = false }
}

onMounted(load)
</script>