<template>
  <el-card>
    <template #header>{{ t('account.apiKeys') }}</template>

    <!-- 批11：原用户/邀请段迁往 UserManagement.vue（系统管理→用户管理）；本组件只留 API 密钥段 -->
    <div>
      <h3 style="font-size: 16px; margin-bottom: 12px">{{ t('account.apiKeys') }}</h3>
      <el-table :data="accounts">
        <el-table-column prop="name" :label="t('common.name')" min-width="150" show-overflow-tooltip />
        <el-table-column prop="exchange" :label="t('account.exchange')" width="120" />
        <el-table-column prop="api_key_hint" :label="t('account.apiKey')" show-overflow-tooltip />
        <el-table-column :label="t('common.status')" width="80">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? t('common.enabled') : t('common.disabled') }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-alert type="info" :closable="false" style="margin-top: 12px">
        {{ t('account.apiKeyHint') }}
      </el-alert>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getAccounts } from '../api'

const { t } = useI18n()
const accounts = ref([])
onMounted(async () => {
  try { accounts.value = await getAccounts() }
  catch { ElMessage.error(t('common.loadFailed')) }
})
</script>
