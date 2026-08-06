<template>
  <el-card>
    <template #header>{{ t('risk.state') }}</template>
    <el-alert :title="state.halted ? t('risk.halted') : t('risk.normal')"
              :type="state.halted ? 'error' : 'success'" show-icon :closable="false" style="margin-bottom: 20px" />
    <el-descriptions :column="1" border>
      <el-descriptions-item label="熔断原因">{{ state.reason || '—' }}</el-descriptions-item>
      <el-descriptions-item label="总回撤上限">{{ (state.rules?.global?.max_drawdown * 100).toFixed(0) }}%</el-descriptions-item>
      <el-descriptions-item label="单日亏损上限">{{ (state.rules?.global?.daily_loss_limit * 100).toFixed(0) }}%</el-descriptions-item>
      <el-descriptions-item label="加密杠杆上限">{{ state.rules?.crypto?.leverage_max }}x</el-descriptions-item>
    </el-descriptions>
    <div style="margin-top: 20px; display: flex; gap: 12px">
      <el-button type="danger" @click="onHalt" :disabled="state.halted">{{ t('risk.halt') }}</el-button>
      <el-button type="success" @click="onResume" :disabled="!state.halted">{{ t('risk.resume') }}</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox, ElMessage } from 'element-plus'
import { getRiskState, riskHalt, riskResume } from '../api'

const { t } = useI18n()
const state = ref({ halted: false, reason: '', rules: { global: { max_drawdown: 0.15, daily_loss_limit: 0.05 }, crypto: { leverage_max: 5 } } })
const load = async () => { state.value = await getRiskState() }
const onHalt = async () => {
  await ElMessageBox.confirm(t('risk.confirmHalt'), { type: 'warning' })
  await riskHalt(); ElMessage.success('已熔断'); load()
}
const onResume = async () => {
  await ElMessageBox.confirm(t('risk.confirmResume'), { type: 'warning' })
  await riskResume(); ElMessage.success('已恢复'); load()
}
onMounted(load)
</script>
