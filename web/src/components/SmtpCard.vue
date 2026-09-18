<template>
  <!-- 批47：邮件多通道表格（smtp_provider——批43 SmsCard 同模式重写；行序即 failover 顺序）。
       原"SMTP 卡"单实例表单退役；测试邮件功能保留（header 图标钮）。 -->
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">{{ t('smtp.title') }}</span>
          <span style="display: flex; gap: 8px; align-items: center">
            <el-input v-model="testTo" :placeholder="t('smtp.testPh')" size="small" style="width: 200px" />
            <IconBtn :icon="Promotion" :loading="testing" :title="t('smtp.test')" @click="sendTest" />
            <IconBtn :icon="Plus" :title="t('alerts.smtpProvAdd')" @click="openAdd" />
          </span>
        </div>
      </template>
      <div class="order-note">{{ t('alerts.smtpProvOrderNote') }}</div>
      <TableShell :data="rows" storage-key="smtp-providers" row-key="id">
        <el-table-column :label="t('alerts.smtpProvDrag')" width="46">   <!-- 列头 menu 图标（批46 形态），:label 保留供列宽持久化键 -->
          <template #header><el-icon :title="t('alerts.smtpProvDrag')" :size="16"><Menu /></el-icon></template>
          <template #default="{ $index }">
            <span class="drag-handle" :draggable="true"
                  :title="t('alerts.smtpProvDrag')"
                  @dragstart="onDragStart($index)" />
          </template>
        </el-table-column>
        <el-table-column prop="name" :label="t('alerts.smtpProvName')" min-width="120" show-overflow-tooltip>
          <template #default="{ row, $index }">
            <span @dragover.prevent @drop="onDrop($index)">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('alerts.smsProvVendor')" width="180" show-overflow-tooltip>   <!-- 批53:列序裁定 名称/运营商/状态/操作——服务器地址收进运营商列(配置项不占表宽) -->
          <template #default="{ row }">{{ row.host }}</template>
        </el-table-column>
        <el-table-column :label="t('alerts.smtpProvStatus')" width="170">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" style="margin: 1px">
              {{ row.enabled ? t('alerts.smtpProvEnabled') : t('alerts.smtpProvDisabled') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="actions" :label="t('common.action')" width="110">
          <template #default="{ row }">
            <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="del(row)" />
          </template>
        </el-table-column>
        <template #empty>{{ t('alerts.smtpProvEmpty') }}</template>
      </TableShell>
    </el-card>

    <!-- 编辑弹窗（批47 单页表单——SMTP 无模板概念；密码批38 三段语义：留空=不改） -->
    <el-dialog v-model="dlg" :close-on-click-modal="false"
               :title="isEdit ? t('alerts.smtpProvEditTitle') : t('alerts.smtpProvAddTitle')" width="520px">
      <el-form label-position="top" style="max-width: 460px">
        <el-form-item :label="t('alerts.smtpProvNameField')">
          <el-input v-model="form.name" :placeholder="t('alerts.smtpProvNamePh')" />
        </el-form-item>
        <el-form-item :label="t('alerts.smtpProvHostField')">
          <el-input v-model="form.host" :placeholder="t('alerts.smtpProvHostPh')" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="10">
            <el-form-item :label="t('alerts.smtpProvPortField')">
              <el-input-number v-model="form.port" v-bind="PORT_INPUT" placeholder="465 / 587" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="14">
            <el-form-item :label="t('alerts.smtpProvSecField')">
              <el-radio-group v-model="form.security">
                <el-radio-button value="auto">{{ t('smtp.secAuto') }}</el-radio-button>
                <el-radio-button value="ssl">SSL</el-radio-button>
                <el-radio-button value="starttls">STARTTLS</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item :label="t('smtp.username')">
          <el-input v-model="form.username" />
        </el-form-item>
        <el-form-item :label="t('smtp.password')">
          <el-input v-model="form.password" type="password" show-password autocomplete="new-password"
                    :placeholder="t('common.phEditNoChange')" />
        </el-form-item>
        <el-form-item :label="t('smtp.from')">
          <el-input v-model="form.from" :placeholder="t('smtp.fromPh')" />
        </el-form-item>
        <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listSmtpProviders, createSmtpProvider, updateSmtpProvider, deleteSmtpProvider,
         reorderSmtpProviders, sendTestEmail, apiErr } from '../api'
import { PORT_INPUT } from '../utils/inputRanges'
import TableShell from './TableShell.vue'
import IconBtn from './IconBtn.vue'
import { Edit, Delete, Plus, Menu, Promotion } from '@element-plus/icons-vue'

const { t } = useI18n()
const rows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const editingId = ref(null)
const saving = ref(false)
const testing = ref(false)
const testTo = ref('')
const emptyForm = () => ({ name: '', host: '', port: 587, security: 'auto', username: '',
                           password: '', from: '', enabled: true })
const form = ref(emptyForm())

const load = async () => {
  try { rows.value = (await listSmtpProviders()).items || [] } catch {}
}

const openAdd = () => { isEdit.value = false; editingId.value = null; form.value = emptyForm(); dlg.value = true }
const openEdit = (row) => {
  isEdit.value = true; editingId.value = row.id
  form.value = { ...row, password: '' }   // 密码永远空起——留空=不改（后端批38 三段语义）
  dlg.value = true
}

const save = async () => {
  saving.value = true
  try {
    const body = { name: form.value.name, host: form.value.host, port: form.value.port,
                   security: form.value.security, username: form.value.username,
                   from: form.value.from, enabled: form.value.enabled }
    if (form.value.password) body.password = form.value.password   // 留空=不发送该键
    if (isEdit.value) await updateSmtpProvider(editingId.value, body)
    else await createSmtpProvider({ ...body, password: form.value.password })
    ElMessage.success(t('common.saveSuccess'))
    dlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const del = async (row) => {
  try {
    await ElMessageBox.confirm(t('alerts.smtpProvDeleteConfirm', { name: row.name }),
                               t('common.delete'), { type: 'warning' })
    await deleteSmtpProvider(row.id)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.detail) ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

// ——— 拖拽重排（批43 SmsCard 同款：乐观重排+失败重拉）———
const onDragStart = (i) => { dragIdx = i }
let dragIdx = -1
const onDrop = async (i) => {
  if (dragIdx < 0 || dragIdx === i) return
  const arr = [...rows.value]
  const [moved] = arr.splice(dragIdx, 1)
  arr.splice(i, 0, moved)
  rows.value = arr   // 乐观
  dragIdx = -1
  try { await reorderSmtpProviders(arr.map(r => r.id)) }
  catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))); await load() }
}

const sendTest = async () => {
  if (!testTo.value || !testTo.value.includes('@')) { ElMessage.warning(t('smtp.testPh')); return }
  testing.value = true
  try {
    await sendTestEmail({ to: testTo.value })
    ElMessage.success(t('smtp.testSent'))
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { testing.value = false }
}
onMounted(load)
</script>

<style scoped>
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
.drag-handle { display: inline-block; width: 12px; height: 10px; cursor: grab;
               background: linear-gradient(180deg, var(--text-secondary) 0 2px, transparent 2px 4px, var(--text-secondary) 4px 6px, transparent 6px 8px, var(--text-secondary) 8px 10px);
               opacity: .55; }
.drag-handle:active { cursor: grabbing; }
</style>
