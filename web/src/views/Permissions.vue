<template>
  <!-- P3-7（web-design 10 §4）：权限管理（仅 admin）——角色×权限键矩阵编辑 + 玻璃盒说明 -->
  <el-card>
    <template #header>{{ t('perm.title') }}</template>
    <el-alert type="info" :closable="false" style="margin-bottom: 14px">{{ t('perm.note') }}</el-alert>
    <el-tabs v-model="role">
      <el-tab-pane v-for="r in ['viewer','analyst','trader','admin']" :key="r" :name="r">
        <template #label><b>{{ r }}</b></template>
        <el-checkbox-group v-model="selected[r]">
          <el-checkbox v-for="k in keys" :key="k" :value="k" style="margin: 6px 14px">{{ k }}</el-checkbox>
        </el-checkbox-group>
      </el-tab-pane>
    </el-tabs>
    <el-button type="primary" @click="save" :loading="saving">{{ t('common.save') }}</el-button>
  </el-card>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api from '../api'
const { t } = useI18n()
const role = ref('viewer')
const keys = ref([])
const selected = ref({ viewer: [], analyst: [], trader: [], admin: [] })
const saving = ref(false)
onMounted(async () => {
  const r = await api.get('/permissions')
  keys.value = r.keys || []
  selected.value = r.roles || {}
})
const save = async () => {
  saving.value = true
  try {
    await api.post(`/permissions/${role.value}`, { permissions: selected.value[role.value] })
    ElMessage.success(t('common.success'))
  } catch { ElMessage.error(t('common.failed')) }
  finally { saving.value = false }
}
</script>
