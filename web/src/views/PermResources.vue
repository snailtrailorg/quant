<template>
  <!-- 批163 v3（用户定版=集成中心同构）：TabsShell 进 header 槽（进去即页签，无标题/无描述行）；
       页签内容=内嵌 el-card（蹭 tokens:185 字体特判防 header 样式连坐——v1 教训），刷新钮在各卡头。 -->
    <TabsShell :tabs="tabs" default-tab="nav" v-slot="slotProps">
      <!-- 菜单页签（唯一可编辑面：分组/排序/显示名覆盖/停用） -->
      <el-card v-if="slotProps.tab === 'nav'">
        <template #header>
          <div style="display:flex; justify-content:space-between; align-items:center">
            <span style="font-weight: 600">{{ t('permRes.listNav') }}</span>
            <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
          </div>
        </template>
        <TableShell :data="rows.nav" storage-key="perm-res-nav">
          <el-table-column prop="id" :label="t('permRes.colId')" min-width="120">
            <template #default="{ row }"><span class="key-id">{{ row.id }}</span></template>
          </el-table-column>
          <el-table-column :label="t('permRes.colRes')" min-width="120">
            <template #default="{ row }">{{ labelOf(row.id) }}</template>
          </el-table-column>
          <el-table-column :label="t('permRes.colGroup')" min-width="100">
            <template #default="{ row }">{{ groupLabel(row.group) }}</template>
          </el-table-column>
          <el-table-column prop="order" :label="t('permRes.colOrder')" width="90" />
          <el-table-column :label="t('permRes.colLabel')" min-width="150">
            <template #default="{ row }">
              <span>{{ row.label?.zh || labelOf(row.id) }}</span>
              <span v-if="row.label?.en && row.label.zh" style="color: var(--text-secondary)"> / {{ row.label.en }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('permRes.colStatus')" width="90">
            <template #default="{ row }">
              <el-tag :type="row.enabled === false ? 'info' : 'success'" size="small">
                {{ row.enabled === false ? t('permRes.disabled') : t('permRes.enabled') }}
              </el-tag>
            </template>
          </el-table-column>
            <el-table-column prop="actions" :label="t('common.action')" width="90">
              <template #default="{ row }">
                <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
              </template>
            </el-table-column>
        </TableShell>
      </el-card>
      <!-- API 键页签（只读+绑定端点反查+锁键标注） -->
      <el-card v-else-if="slotProps.tab === 'api'">
        <template #header>
          <div style="display:flex; justify-content:space-between; align-items:center">
            <span style="font-weight: 600">{{ t('permRes.listApi') }}</span>
            <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
          </div>
        </template>
        <TableShell :data="rows.api" storage-key="perm-res-api">
          <el-table-column prop="key" :label="t('permRes.colId')" min-width="170">
            <template #default="{ row }"><span class="key-id">{{ row.key }}</span></template>
          </el-table-column>
          <el-table-column :label="t('permRes.colRes')" min-width="140">
            <template #default="{ row }">
              {{ keyLabel(row.key) }}<span v-if="row.locked" :title="t('permRes.locked')"> <el-icon :size="12"><Lock /></el-icon></span>
            </template>
          </el-table-column>
          <el-table-column :label="t('permRes.colEndpoints')" min-width="420">
            <template #default="{ row }">
              <div v-for="ep in row.endpoints" :key="ep" class="ep-line" :title="ep">{{ ep }}</div>
              <span v-if="!row.endpoints.length" style="color: var(--text-secondary)">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="t('permRes.colStatus')" width="90">
            <template #default>
              <el-tooltip :content="t('permRes.readonly')" placement="top">
                <el-tag type="info" size="small">{{ t('permRes.readonlyShort') }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
        </TableShell>
      </el-card>
      <!-- 市场键页签（只读） -->
      <el-card v-else>
        <template #header>
          <div style="display:flex; justify-content:space-between; align-items:center">
            <span style="font-weight: 600">{{ t('permRes.listMkt') }}</span>
            <IconBtn :icon="Refresh" :title="t('common.refresh')" @click="load" />
          </div>
        </template>
        <TableShell :data="rows.market_op.map(k => ({ key: k }))" storage-key="perm-res-market">
          <el-table-column prop="key" :label="t('permRes.colId')" min-width="130">
            <template #default="{ row }"><span class="key-id">{{ row.key }}</span></template>
          </el-table-column>
          <el-table-column :label="t('permRes.colRes')" min-width="120">
            <template #default="{ row }">{{ marketLabel(row.key) }}</template>
          </el-table-column>
          <el-table-column :label="t('permRes.colStatus')" width="90">
            <template #default>
              <el-tooltip :content="t('permRes.readonly')" placement="top">
                <el-tag type="info" size="small">{{ t('permRes.readonlyShort') }}</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
        </TableShell>
      </el-card>
    </TabsShell>

  <!-- 编辑弹窗（nav 条目四字段；条目集不可增删红线） -->
    <el-dialog v-model="editDlg" :close-on-click-modal="false" :title="t('permRes.editTitle')" width="440px">
      <el-form label-width="110px">
        <el-form-item :label="t('permRes.colId')"><el-input :model-value="editing.id" disabled /></el-form-item>
        <el-form-item :label="t('permRes.colRes')"><span>{{ labelOf(editing.id) }}</span></el-form-item>
        <el-form-item :label="t('permRes.fGroup')"><el-input v-model="editing.group_key" /></el-form-item>
        <el-form-item :label="t('permRes.fOrder')"><el-input-number v-model="editing.sort_order" :min="0" :step="1" /></el-form-item>
        <el-form-item :label="t('permRes.fLabelZh')"><el-input v-model="editing.label_zh" :placeholder="labelOf(editing.id)" /></el-form-item>
        <el-form-item :label="t('permRes.fLabelEn')"><el-input v-model="editing.label_en" :placeholder="labelEnOf(editing.id)" /></el-form-item>
        <el-form-item :label="t('permRes.fEnabled')"><el-switch v-model="editing.enabled_on" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import api, { apiErr } from '../api'
import TableShell from '../components/TableShell.vue'
import TabsShell from '../components/TabsShell.vue'
import IconBtn from '../components/IconBtn.vue'
import { Edit, Refresh, Lock } from '@element-plus/icons-vue'

const { t, te } = useI18n()
const tabs = [
  { key: 'nav', i18nKey: 'permRes.tabNav' },
  { key: 'api', i18nKey: 'permRes.tabApi' },
  { key: 'market', i18nKey: 'permRes.tabMarket' },
]
const rows = ref({ api: [], nav: [], market_op: [], bindings_total: 0 })
const editDlg = ref(false)
const editing = ref({})
const saving = ref(false)

// 组码→中文（nav 组词条既有；DB 覆盖层新组名无词条回落原码）
const GROUP_KEYS = { base: 'dashboard', research: 'gResearch', live: 'gLive', riskgrp: 'gRisk', ops: 'gOps' }
const groupLabel = g => (te(`nav.${GROUP_KEYS[g] || g}`) ? t(`nav.${GROUP_KEYS[g] || g}`) : g)
// api 键中文名（PermMatrix 同款 perm.key_* 词条链）+市场键（perm.mk_*）
const keyLabel = k => (te(`perm.key_${k}`) ? t(`perm.key_${k}`) : k)
const marketLabel = k => (te(`perm.mk_${k}`) ? t(`perm.mk_${k}`) : k)
// 显示名兜底链：覆盖 label → nav.* 词条（kebab id→camel 键归一——nav.liveTask 类）→ 原 id
const _ck = id => id.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
const labelOf = id => (te(`nav.${_ck(id)}`) ? t(`nav.${_ck(id)}`) : id)
const labelEnOf = id => (te(`nav.${_ck(id)}`, 'en') ? t(`nav.${_ck(id)}`, 'en') : id)

const load = async () => {
  try { rows.value = await api.get('/perm-resources') }
  catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
}
onMounted(load)

const openEdit = (row) => {
  editing.value = { id: row.id, group_key: row.group, sort_order: row.order,
                    label_zh: row.label?.zh || '', label_en: row.label?.en || '',
                    enabled_on: row.enabled !== false }
  editDlg.value = true
}

const save = async () => {
  saving.value = true
  try {
    const e = editing.value
    const label_json = (e.label_zh || e.label_en)
      ? { zh: e.label_zh || undefined, en: e.label_en || undefined } : null
    await api.patch(`/perm-resources/nav/${e.id}`, {
      group_key: e.group_key || null, sort_order: e.sort_order ?? null,
      label_json, enabled: e.enabled_on })
    editDlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (err) { ElMessage.error(apiErr(err, t('common.saveFailed'))) }
  finally { saving.value = false }
}
</script>
<style scoped>
.bind-note { font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-2); }
.key-id { font-family: var(--font-mono, monospace); font-size: var(--fs-foot); color: var(--text-secondary); }
.ep-line { font-family: var(--font-mono, monospace); font-size: var(--fs-foot); line-height: 1.5;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 420px; }
</style>
