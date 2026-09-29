<template>
  <!-- 批 62c：SM 对账（/dataops 第四 tab）——批 79 起 shadow 行情对账已删，页签瘦身为仅 SM 差集 -->
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('quality.title') }}</span>
        <RefreshBtn :loading="loading" @refresh="loadAll" />
      </div>
    </template>

    <div style="font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-2)">{{ t('quality.smScopeNote') }}</div>
    <el-row :gutter="12">
      <el-col :span="12">
        <div style="font-weight: 600; margin-bottom: 4px">{{ t('quality.smOnly') }} ({{ (sm.sm_only || []).length }})</div>
        <div style="font-size: var(--fs-foot)">{{ (sm.sm_only || []).slice(0, 20).join(' ') || '—' }}</div>
      </el-col>
      <el-col :span="12">
        <div style="font-weight: 600; margin-bottom: 4px">{{ t('quality.staticOnly') }} ({{ (sm.static_only || []).length }})</div>
        <div style="font-size: var(--fs-foot)">{{ (sm.static_only || []).slice(0, 20).join(' ') || '—' }}</div>
      </el-col>
    </el-row>
  </el-card>
</template>

<script setup>
import RefreshBtn from '../components/RefreshBtn.vue'
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSmReconcile } from '../api'

const { t } = useI18n()
const loading = ref(false)
const sm = ref({})

const loadAll = async () => {
  loading.value = true
  try {
    const d = await getSmReconcile()
    sm.value = d || {}
  } catch {} finally { loading.value = false }
}
onMounted(loadAll)
</script>
