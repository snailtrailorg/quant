<template>
  <div>
    <!-- 批24 迭代十六 hotfix4（用户裁定：条目级整合非页面拼接）：邮件发送记录映射为日志条目混排进本表
         （level=status 映射/module=email/msg=收件人+主题+错误），双源合并按时间倒序；
         分组筛选（级别+模块——模块 distinct 预取自然含 email）+导出（合并行） -->
    <div style="display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-bottom: var(--sp-3)">
      <RowFilter v-model="rowFilter" :groups="filterGroups" :title="t('common.filter')" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExport" />
    </div>
    <el-alert v-if="noPerm" type="warning" :title="t('log.noPerm')" :closable="false" style="margin-bottom: var(--sp-3)" />
    <TableShell v-else :data="filteredLogs" fill infinite :more-text="moreText" :loading="loading"
                @load-more="onLoadMore" storage-key="logs-run">
      <el-table-column prop="ts" :label="t('common.time')" width="220">   <!-- 批53:固定型 width 退出弹性(原 min-width 220 在 flex 尾列表吸走富余→实测 600px 荒谬) -->
        <template #default="{ row }">{{ fmtTime.full(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="level" :label="t('log.level')" width="90">
        <template #default="{ row }">
          <el-tag :type="row.level === 'ERROR' ? 'danger' : row.level === 'WARN' ? 'warning' : 'info'">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="module" :label="t('log.module')" width="140" />
      <el-table-column prop="msg" :label="t('log.content')" show-overflow-tooltip />
    </TableShell>

  </div>
</template>

<script setup>
import TableShell from '../components/TableShell.vue'
import RowFilter from '../components/RowFilter.vue'
import IconBtn from '../components/IconBtn.vue'
import { fmtTime } from '../utils/fmtTime'
import { exportCsv } from '../exportCsv'
import { Download } from '@element-plus/icons-vue'
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { apiErr } from '../api'
const { t } = useI18n()

// 批32：游标懒加载（每页 100）+level/module 后端筛选（用户裁定：日志是查问题用的，筛不准=白筛）
const logs = ref([])
const next = ref(null)
const loading = ref(true)
const loadingMore = ref(false)
let reqId = 0   // 批32 A-P1-5：筛选变更重置游标，在途响应 reqId 不匹配即丢弃（防旧页后到覆盖新筛选）
const rowFilter = ref({})
const levelOptions = ['ERROR', 'WARN', 'INFO'].map(v => ({ value: v, label: v }))
const moduleOptions = ref([])   // 批32：后端首屏附带全量（原从已加载行 distinct——筛历史半残）
const filterGroups = computed(() => [
  { key: 'level', label: t('log.level'), options: levelOptions },
  { key: 'module', label: t('log.module'), options: moduleOptions.value },
])
const fetchPage = async (before) => {
  const my = ++reqId
  const params = { }
  if (before) params.before = before
  const { level = [], module = [] } = rowFilter.value
  if (level.length) params.level = level
  if (module.length) params.module = module
  try {
    const r = await api.get('/log', { params })
    if (my !== reqId) return   // 过期响应丢弃
    if (before) logs.value.push(...r.logs)
    else { logs.value = r.logs; if (r.modules) moduleOptions.value = r.modules.map(v => ({ value: v, label: v })) }
    next.value = r.next
  } catch (e) {
    if (e?.code === 'PERM_DENIED') { noPerm.value = true; return }   // A-P1-1：批25"403 显式横条"复活（后端批32 补 PERM_DENIED 码）
    ElMessage.error(apiErr(e, ''))
  } finally {
    if (my === reqId) { loading.value = false; loadingMore.value = false }
  }
}
const onLoadMore = () => {
  if (!next.value || loadingMore.value || loading.value) return
  loadingMore.value = true
  fetchPage(next.value)
}
const moreText = computed(() => loadingMore.value ? t('common.loading')
  : (!next.value && logs.value.length ? t('common.noMore') : ''))
watch(rowFilter, () => { loading.value = true; next.value = null; fetchPage() }, { deep: true })   // 筛选变更=重置游标重拉首屏（next 先清防"没有更多"闪现——B-P2-4）
const filteredLogs = computed(() => logs.value)   // 筛选已在后端（保留 computed 名兼容导出引用）
const onExport = () => exportCsv('run_logs',
  [t('common.time'), t('log.level'), t('log.module'), t('log.content')],
  filteredLogs.value.map(l => [fmtTime.full(l.ts), l.level, l.module, l.msg]))
const noPerm = ref(false)
onMounted(async () => {
  try { await fetchPage() }
  // 批25：运行日志=管理员面——403 显式提示非静默空表；批27-19：其余错误（500/网络）也不再静默
  catch (e) {
    if (e?.response?.status === 403) noPerm.value = true
    else ElMessage.error(apiErr(e, t('common.loadFailed')))
  }
})
</script>
