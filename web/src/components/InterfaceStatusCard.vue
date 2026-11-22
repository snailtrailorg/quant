<template>
  <el-card v-if="rows.length" class="mb">
    <div class="hdr">运行态（只读）</div>
    <el-table :data="rows" size="small" border>
      <el-table-column prop="code" label="接口" min-width="120" />
      <el-table-column prop="provider" label="provider" min-width="100" />
      <el-table-column label="进程" min-width="80">
        <template #default="{ row }"><span :class="row.running ? 'on' : 'off'">{{ row.running ? '运行' : '未运行' }}</span></template>
      </el-table-column>
      <el-table-column label="工作" min-width="80">
        <template #default="{ row }"><span :class="row.working ? 'on' : 'off'">{{ row.working ? '工作中' : '空闲' }}</span></template>
      </el-table-column>
      <el-table-column label="连接" min-width="80">
        <template #default="{ row }"><span :class="row.connected ? 'on' : 'off'">{{ row.connected ? '已连' : '未连' }}</span></template>
      </el-table-column>
      <el-table-column label="配置" min-width="120">
        <template #default="{ row }">
          <span v-if="row.virtual" class="off">—</span>
          <span v-else :class="row.config_ok ? 'on' : 'off'">{{ row.config_ok ? '完整' : missingText(row.config_missing) }}</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../api'

const props = defineProps({ kind: { type: String, required: true } })
const MISSING_LABEL = { carrier: '缺载体', credentials: '缺凭证', mapping: '缺映射' }
const missingText = list => (list || []).map(x => MISSING_LABEL[x] || x).join('/') || '未完成'
const rows = ref([])
let timer = null
async function load() {
  try { rows.value = (await api.get('/interfaces/status', { params: { kind: props.kind } })).items || [] }
  catch { rows.value = [] }
}
onMounted(() => { load(); timer = setInterval(load, 30000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.mb { margin-bottom: var(--sp-3); }
.hdr { font-weight: 600; margin-bottom: var(--sp-2); }
.on { color: var(--success); }
.off { color: var(--text-3); }
</style>
