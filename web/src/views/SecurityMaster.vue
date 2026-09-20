<template>
  <!-- 批 56a·M1：标的属性查询页（只读——主档+时变时间线+时段表；29 号 §四面三件套） -->
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; gap: 8px; align-items: center">
        <el-input v-model="symbol" :placeholder="t('interfaces.secPh')" style="width: 260px"
                  @keyup.enter="load" clearable />
        <el-button type="primary" @click="load" :loading="loading">{{ t('interfaces.secSearch') }}</el-button>
      </div>
    </el-card>

    <el-alert v-if="errMsg" type="warning" :title="errMsg" show-icon :closable="false"
              style="margin-bottom: 12px" />

    <template v-if="data">
      <el-card shadow="never" style="margin-bottom: 12px">
        <template #header><span style="font-weight: 600">{{ t('interfaces.secTitle') }}</span></template>
        <el-descriptions :column="4" border size="small">
          <el-descriptions-item :label="t('interfaces.secColName')">{{ data.attr.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Code">{{ data.attr.vt_symbol }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColMarket')">{{ t('interfaces.market' + cap(data.attr.market)) }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColExchange')">{{ data.attr.exchange }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColCategory')">{{ t('interfaces.cat' + cap(data.attr.category)) }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColIndustry')">{{ data.attr.industry || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColMultiplier')">
            <span>{{ data.attr.multiplier }}</span>
            <el-tooltip :content="t('interfaces.secMultiplierTip')" placement="top">
              <el-icon style="margin-left: 4px; color: var(--text-secondary)"><InfoFilled /></el-icon>
            </el-tooltip>
          </el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColTick')">{{ data.attr.tick_size }}</el-descriptions-item>
          <el-descriptions-item :label="t('interfaces.secColPhase')">{{ data.attr.trade_phase }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" style="margin-bottom: 12px">
        <template #header><span style="font-weight: 600">{{ t('interfaces.secTimelineTitle') }}</span></template>
        <el-timeline v-if="data.states.length" style="padding-left: 4px">
          <el-timeline-item v-for="(s, i) in data.states" :key="i" :timestamp="s.effective_from" placement="top">
            <el-tag size="small" style="margin-right: 6px">{{ s.kind }}</el-tag>
            <code style="font-size: var(--fs-foot)">{{ JSON.stringify(s.value) }}</code>
          </el-timeline-item>
        </el-timeline>
        <div v-else class="empty-note">{{ t('interfaces.secTimelineEmpty') }}</div>
      </el-card>

      <el-card shadow="never">
        <template #header><span style="font-weight: 600">{{ t('interfaces.secSessionsTitle') }}</span></template>
        <el-table :data="data.sessions" size="small" style="width: 100%">
          <el-table-column prop="phase" :label="t('interfaces.secSesPhase')" min-width="110" />
          <el-table-column prop="start" :label="t('interfaces.secSesStart')" min-width="90" />
          <el-table-column prop="end" :label="t('interfaces.secSesEnd')" min-width="90" />
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { InfoFilled } from '@element-plus/icons-vue'
import api, { apiErr } from '../api'
import { ElMessage } from 'element-plus'

const { t } = useI18n()
const symbol = ref('')
const data = ref(null)
const loading = ref(false)
const errMsg = ref('')

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)

const load = async () => {
  if (!symbol.value.trim()) return
  loading.value = true
  errMsg.value = ''
  data.value = null
  try {
    data.value = await api.get(`/security/${symbol.value.trim()}`)
  } catch (e) {
    const code = e?.response?.data?.code
    if (code === 'SECURITY_NOT_FOUND') errMsg.value = t('interfaces.secEmpty', { sym: symbol.value.trim() })
    else ElMessage.error(apiErr(e))
  } finally { loading.value = false }
}
</script>

<style scoped>
.empty-note { color: var(--text-secondary); padding: 16px 0; text-align: center; font-size: var(--fs-foot); }
</style>
