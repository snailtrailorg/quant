<template>
  <!-- P1-8（06 宝藏#3）：SELL 保底明示——冻结/盲视/熔断期"BUY 已拒 SELL 放行" -->
  <el-alert v-if="active" :title="t('sellGuard.text', { reason })" type="warning" show-icon :closable="false" style="margin-bottom: 14px" />
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRiskState, getLiveTasks } from '../api'
const { t } = useI18n()
const halted = ref(false)
const anyFrozen = ref(false)
const active = computed(() => halted.value || anyFrozen.value)
const reason = computed(() => halted.value ? t('sellGuard.halted') : t('sellGuard.frozen'))
onMounted(async () => {
  try { halted.value = !!(await getRiskState()).halted } catch {}
  try { anyFrozen.value = (await getLiveTasks()).some(x => x.frozen) } catch {}
})
</script>
