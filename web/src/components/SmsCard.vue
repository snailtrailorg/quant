<template>
  <!-- 批43：短信通道表格（多服务商容灾——行序即 failover 顺序，纯拖拽单层）。
       批37 原单卡片 SmsCard 重写；编辑弹窗两页签复用批40 两 section 词条。 -->
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">{{ t('tabs.sms') }}</span>
          <IconBtn :icon="Plus" :title="t('alerts.smsProvAdd')" @click="openAdd" />
        </div>
      </template>
      <div class="order-note">{{ t('alerts.smsProvOrderNote') }}</div>
      <TableShell :data="rows" storage-key="sms-providers" row-key="id">
        <el-table-column :label="t('alerts.smsProvDrag')" width="46">   <!-- 批46：视觉列头换 menu 图标（#header slot 优先渲染），:label 保留供 TableShell 列宽持久化键（快审 A-P1-2：移除则 colKey=undefined 互踩） -->
          <template #header><el-icon :title="t('alerts.smsProvDrag')" :size="16"><Menu /></el-icon></template>
          <template #default="{ $index }">
            <span class="drag-handle" :draggable="true"
                  :title="t('alerts.smsProvDrag')"
                  @dragstart="onDragStart($index)" />
          </template>
        </el-table-column>
        <el-table-column prop="name" :label="t('alerts.smsProvName')" min-width="120" show-overflow-tooltip>
          <template #default="{ row, $index }">
            <span @dragover.prevent @drop="onDrop($index)">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('alerts.smsProvVendor')" width="100">
          <template #default="{ row }">{{ t('alerts.smsProvVendors.' + row.provider) }}</template>
        </el-table-column>
        <el-table-column :label="t('alerts.smsProvStatus')" width="170">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" style="margin: 1px">
              {{ row.enabled ? t('alerts.smsProvEnabled') : t('alerts.smsProvDisabled') }}
            </el-tag>
            <el-tag :type="row.credentials_set ? 'success' : 'warning'" size="small" style="margin: 1px">
              {{ row.credentials_set ? t('alerts.smsProvCredOk') : t('alerts.smsProvCredNo') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="actions" :label="t('common.action')" width="110">
          <template #default="{ row }">
            <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="del(row)" />
          </template>
        </el-table-column>
        <template #empty>{{ t('alerts.smsProvEmpty') }}</template>
      </TableShell>
    </el-card>

    <!-- 编辑弹窗（两页签——基本参数/短信模板；批38 三段语义：密钥留空=不改） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false"
               :title="isEdit ? t('alerts.smsProvEditTitle') : t('alerts.smsProvAddTitle')" width="560px">
      <TabsShell :tabs="dlgTabs" default-tab="basic" routing="false" v-slot="slotProps">
        <el-form v-if="slotProps.tab === 'basic'" label-position="top" style="max-width: 480px">
          <el-form-item :label="t('alerts.smsProvNameField')">
            <el-input v-model="form.name" :placeholder="t('alerts.smsProvNamePh')" />
          </el-form-item>
          <el-form-item label="AccessKey ID"><el-input v-model="form.access_key_id" :placeholder="ph()" /></el-form-item>
          <el-form-item label="AccessKey Secret"><el-input v-model="form.access_key_secret" type="password" show-password :placeholder="ph()" autocomplete="new-password" /></el-form-item>
          <el-form-item :label="t('alerts.signName')"><el-input v-model="form.sign_name" /></el-form-item>
          <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
        </el-form>
        <el-form v-else label-position="top" style="max-width: 480px">
          <el-form-item :label="t('alerts.tplCode')">
            <el-input v-model="form.alert_template_code" :placeholder="t('alerts.phAlertTpl')" />
            <el-popover placement="right" :width="420" trigger="click">
              <template #reference>
                <el-link type="primary" :underline="false" style="font-size: var(--fs-foot); margin-top: 2px">{{ t('alerts.tplContentHint') }}</el-link>
              </template>
              <div class="tpl-body">{{ t('alerts.tplBodyAlert') }}</div>
              <div class="tpl-note">{{ t('alerts.tplContentNote') }}</div>
            </el-popover>
          </el-form-item>
          <el-form-item :label="t('alerts.verifyTplCode')">
            <el-input v-model="form.verify_template_code" :placeholder="t('alerts.phVerifyTpl')" />
            <el-popover placement="right" :width="420" trigger="click">
              <template #reference>
                <el-link type="primary" :underline="false" style="font-size: var(--fs-foot); margin-top: 2px">{{ t('alerts.tplContentHint') }}</el-link>
              </template>
              <div class="tpl-body">{{ t('alerts.tplBodyVerify') }}</div>
              <div class="tpl-note">{{ t('alerts.tplContentNote') }}</div>
            </el-popover>
          </el-form-item>
        </el-form>
      </TabsShell>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'
import TableShell from './TableShell.vue'
import TabsShell from './TabsShell.vue'
import IconBtn from './IconBtn.vue'
import { Edit, Delete, Plus, Menu } from '@element-plus/icons-vue'

const { t } = useI18n()
const dlgTabs = [
  { key: 'basic', i18nKey: 'alerts.smsSecBasic' },
  { key: 'tpl', i18nKey: 'alerts.smsSecTpl' },
]
const rows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const saving = ref(false)
const credSet = ref(false)   // 编辑态：已有密钥（placeholder 提示用）
const ph = () => (isEdit.value && credSet.value) ? t('alerts.phKeepBlank') : ''
const form = reactive(emptyForm())
function emptyForm() {
  return { name: '', access_key_id: '', access_key_secret: '', sign_name: '',
           alert_template_code: '', verify_template_code: '', enabled: true }
}

const load = async () => {
  try { rows.value = (await api.get('/alerts/sms-providers')).items || [] }
  catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
}
onMounted(load)

// —— 拖拽（HTML5 原生，列模板内实现——TableShell 零改动）——
let dragIdx = -1
const onDragStart = (idx) => { dragIdx = idx }
const onDrop = async (idx) => {
  if (dragIdx < 0 || dragIdx === idx) return
  const arr = [...rows.value]
  const [moved] = arr.splice(dragIdx, 1)
  arr.splice(idx, 0, moved)
  rows.value = arr   // 乐观重排
  dragIdx = -1
  try { await api.post('/alerts/sms-providers/reorder', { ids: arr.map(r => r.id) }) }
  catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))); await load() }
}

const openAdd = () => {
  isEdit.value = false; credSet.value = false
  Object.assign(form, emptyForm())
  dlg.value = true
}
const openEdit = (row) => {
  isEdit.value = true; credSet.value = row.credentials_set
  Object.assign(form, emptyForm(), {
    id: row.id,
    name: row.name, sign_name: row.sign_name,
    alert_template_code: row.alert_template_code, verify_template_code: row.verify_template_code,
    enabled: row.enabled,   // 密钥对不回显——留空=不改
  })
  dlg.value = true
}
const save = async () => {
  saving.value = true
  try {
    const body = { name: form.name, sign_name: form.sign_name,
                   alert_template_code: form.alert_template_code,
                   verify_template_code: form.verify_template_code, enabled: form.enabled }
    if (form.access_key_id) body.access_key_id = form.access_key_id
    if (form.access_key_secret) body.access_key_secret = form.access_key_secret
    if (isEdit.value) await api.post(`/alerts/sms-providers/${form.id}`, body)
    else await api.post('/alerts/sms-providers', body)
    dlg.value = false
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
const del = async (row) => {
  try {
    await ElMessageBox.confirm(t('alerts.smsProvDeleteConfirm', { name: row.name }),
                               t('common.delete'), { type: 'warning' })
    await api.delete(`/alerts/sms-providers/${row.id}`)
    ElMessage.success(t('common.success'))
    await load()
  } catch (e) { if (e?.detail) ElMessage.error(e.detail) }
}
</script>
<style scoped>
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
.drag-handle { display: inline-block; width: 12px; height: 10px; cursor: grab;
               background: linear-gradient(180deg, var(--text-secondary) 0 2px, transparent 2px 4px, var(--text-secondary) 4px 6px, transparent 6px 8px, var(--text-secondary) 8px 10px);   /* 批46：90deg→180deg 竖线转横线（快审 A-P1-1：色标收进 10px 盒高三线全显——原 10-12px 段被裁只显两线） */
               opacity: .55; }
.drag-handle:active { cursor: grabbing; }
.tpl-body { font-family: var(--font-mono, monospace); font-size: var(--fs-foot); padding: 4px; word-break: break-all;
            background: var(--fill-weak, transparent); border-radius: 4px; }
.tpl-note { font-size: var(--fs-foot); color: var(--text-secondary); margin-top: 6px; line-height: 1.5; }
</style>
