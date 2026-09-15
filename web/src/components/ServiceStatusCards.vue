<template>
  <!-- 批22：系统服务状态卡片组（每 systemd unit 一卡） -->
  <!-- 批24 结构统一：组标题从卡外裸 h4 归位 el-card header（用户管理式——批23 表头规则同构） -->
  <el-card>
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center"><span>{{ t('sysmon.services') }}</span></div>
    </template>
    <div class="card-grid">
      <el-card v-for="(st, unit) in units" :key="unit" shadow="never" class="status-card">
        <div class="card-head">
          <span class="card-name">{{ shortName(unit) }}</span>
          <el-tag :type="isActive(st) ? 'success' : 'danger'" size="small" effect="light">
            {{ isActive(st) ? t('sysmon.active') : t('sysmon.inactive') }}
          </el-tag>
        </div>
        <div class="card-meta">{{ t('sysmon.restarts') }}: {{ st.NRestarts || 0 }}</div>
      </el-card>
    </div>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getHealthComponents } from '../api'

const { t } = useI18n()
const units = ref({})
let pollTimer = null

const isActive = st => st && st.ActiveState === 'active'
const shortName = u => u.replace(/^quant-/, '').replace(/@quant\.service$/, '')

const load = async () => {
  try {
    const u = (await getHealthComponents()).units || {}
    const sig = JSON.stringify(u)
    if (sig !== lastSig) { lastSig = sig; units.value = u }   // 数据未变不赋值，轮询视觉连续
  } catch {}
}
let lastSig = ''
onMounted(() => { load(); pollTimer = setInterval(load, 30000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--sp-3); }   /* 每行 3 卡随容器宽等分伸缩 */
.status-card { border: 1px solid var(--border-weak); }
.card-head { display: flex; justify-content: space-between; align-items: center; }
.card-name { font-size: var(--fs-label); font-weight: 600; }
.card-meta { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: var(--sp-1); }
</style>
