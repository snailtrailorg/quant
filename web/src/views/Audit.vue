<template>
  <!-- 批23：header 左标题/右动作组（筛选输入→删除→导出）；表加 selection 批删（confirm 数量回显）。
       筛选=本地 computed 即时生效（原表单+查询/重置按钮退役）；导出 IconBtn 化收进动作组。 -->
  <ContentCard :title="t('audit.listTitle')">
    <!-- 批24 迭代十六原拆卡壳，批163 §8 B 类复归：Observe 页签壳（header 只住页签栏）下工作区直属内容卡——
         动作序=筛选→导出（批25：删除退役）。 -->
    <template #actions>
      <RowFilter v-model="rowFilter" :groups="filterGroups" :title="t('common.filter')" />
      <IconBtn size="small" :icon="Download" :title="t('common.export')" @click="onExportCsv" />
    </template>
    <TableShell :data="logs" fill infinite :more-text="moreText" :loading="loading"
                @load-more="onLoadMore" storage-key="audit">
      <el-table-column prop="ts" :label="t('common.time')" width="220">   <!-- 批53:固定退出弹性(实测 428) -->
        <template #default="{ row }">{{ fmtTime.full(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="actor" :label="t('audit.actor')" min-width="140" show-overflow-tooltip />
      <el-table-column prop="action" :label="t('common.action')" width="170" />
      <el-table-column prop="target" :label="t('audit.target')" min-width="150" />
      <el-table-column prop="detail" :label="t('common.detail')" show-overflow-tooltip />
    </TableShell>
  </ContentCard>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import TableShell from '../components/TableShell.vue'
import ContentCard from '../components/ContentCard.vue'
import { fmtTime } from '../utils/fmtTime'
import api, { apiErr } from '../api'
import IconBtn from '../components/IconBtn.vue'
import RowFilter from '../components/RowFilter.vue'
import { exportCsv as exportCsvUtil } from '../exportCsv'
import { Download } from '@element-plus/icons-vue'
const { t } = useI18n()
// 批32：游标懒加载（每页 100）+actor/action 后端筛选（原两维前端筛已加载行——同病同治）
const logs = ref([])
const next = ref(null)
const loading = ref(true)
const loadingMore = ref(false)
let reqId = 0   // 在途响应守卫（A-P1-5）
const rowFilter = ref({})
const actorOptions = ref([])   // 后端首屏附带全量 DISTINCT（原从已加载行 distinct）
const actionOptions = ref([])
const filterGroups = computed(() => [
  { key: 'actor', label: t('audit.actor'), options: actorOptions.value },
  { key: 'action', label: t('common.action'), options: actionOptions.value },
])
const fetchPage = async (before) => {
  const my = ++reqId
  const params = {}
  if (before) params.before = before
  const { actor = [], action = [] } = rowFilter.value
  if (actor.length) params.actor = actor
  if (action.length) params.action = action
  try {
    const r = await api.get('/audit', { params })
    if (my !== reqId) return
    if (before) logs.value.push(...r.logs)
    else {
      logs.value = r.logs
      if (r.actors) actorOptions.value = r.actors.map(v => ({ value: v, label: v }))
      if (r.actions) actionOptions.value = r.actions.map(v => ({ value: v, label: v }))
    }
    next.value = r.next
  } catch (e) { ElMessage.error(apiErr(e, '')) } finally {
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
watch(rowFilter, () => { loading.value = true; next.value = null; fetchPage() }, { deep: true })   // B-P2-4：next 先清防抖动
const filteredLogs = computed(() => logs.value)   // 筛选已在后端（保留名兼容导出）
onMounted(fetchPage)

// 批23：批量删（后端留痕 audit_delete；audit 自删留痕行在删后写入不被波及）

// P3-5(05 §5.10):审计导出 CSV(合规刚需)
const onExportCsv = () => exportCsvUtil('audit',
  [t('common.time'), t('audit.actor'), t('common.action'), t('audit.target'), t('common.detail')],
  filteredLogs.value.map(a => [a.ts, a.actor, a.action, a.target, a.detail]))
</script>
