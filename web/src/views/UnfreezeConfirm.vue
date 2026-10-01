<template>
  <!-- 批 76 F2：冻结解冻确认页（IM 发起 → Web 登录态确认；token 一次性，10 分钟有效） -->
  <div class="auth-page">
    <el-card style="width: 460px">
      <h2 style="text-align: center; font-size: var(--fs-page); font-weight: 700">
        {{ t('unfreezeConfirm.title') }}
      </h2>
      <div v-if="state === 'loading'" style="text-align: center; color: var(--text-secondary); padding: var(--sp-4) 0">
        {{ t('unfreezeConfirm.loading') }}
      </div><div v-else-if="state === 'failed'" style="text-align: center; padding: var(--sp-4) 0">
        <div style="font-size: calc(var(--fs-kpi) * 1.4); line-height: 1">⚠️</div>
        <p style="margin: var(--sp-2) 0">{{ t('unfreezeConfirm.failed') }}</p>
        <el-button type="primary" @click="$router.push('/live-task')">{{ t('unfreezeConfirm.backTask') }}</el-button>
      </div>
      <div v-else-if="state === 'done'" style="text-align: center; padding: var(--sp-4) 0">
        <div style="font-size: calc(var(--fs-kpi) * 1.4); line-height: 1">✅</div>
        <p style="margin: var(--sp-2) 0">{{ t('unfreezeConfirm.done') }}</p>
        <el-button type="primary" @click="$router.push('/live-task')">{{ t('unfreezeConfirm.backTask') }}</el-button>
      </div>
      <div v-else>
        <el-descriptions :column="1" border size="small" style="margin-bottom: var(--sp-3)">
          <el-descriptions-item :label="t('unfreezeConfirm.task')">{{ info.tid }}</el-descriptions-item>
          <el-descriptions-item :label="t('unfreezeConfirm.symbol')">{{ info.symbol || '-' }}</el-descriptions-item>
          <el-descriptions-item :label="t('unfreezeConfirm.reason')">{{ reasonText }}</el-descriptions-item>
          <el-descriptions-item :label="t('unfreezeConfirm.initiator')">{{ info.initiator || '-' }}</el-descriptions-item>
        </el-descriptions>
        <el-alert :title="t('unfreezeConfirm.tip')" type="warning" :closable="false" show-icon style="margin-bottom: var(--sp-3)" />
        <div style="display: flex; gap: var(--sp-2); justify-content: flex-end">
          <el-button @click="$router.push('/live-task')">{{ t('unfreezeConfirm.cancel') }}</el-button>
          <el-button type="danger" :loading="submitting" @click="submit">{{ t('unfreezeConfirm.confirm') }}</el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { apiErr } from '../api'

const { t } = useI18n()
const route = useRoute()
const state = ref('loading')     // loading | ready | done | failed
const info = ref({})
const submitting = ref(false)

const reasonText = computed(() =>
  t(`unfreezeConfirm.type_${info.value.freeze_type || 'unknown'}`))

onMounted(async () => {
  const token = route.query.token
  if (!token) { state.value = 'failed'; return }
  try {
    info.value = (await api.get('/live-task/unfreeze-request', { params: { token } })) || {}
    state.value = 'ready'
  } catch { state.value = 'failed' }
})

async function submit () {
  submitting.value = true
  try {
    await api.post(`/live-task/${info.value.tid}/unfreeze`, { confirm_token: route.query.token })
    state.value = 'done'
  } catch (e) {
    ElMessage.error(apiErr(e, t('unfreezeConfirm.failed')))
  } finally { submitting.value = false }
}
</script>
