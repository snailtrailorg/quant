<template>
  <!-- 运行配置卡(从 SystemConfig 拆出通用配置;设置·运行配置 tab 用,批 1 归位重组)
       批35：值列只显化——纯文本"当前值"+编辑收编行内图标钮弹窗（批16 操作收编/批21 IconBtn 同模式） -->
  <el-card>
    <template #header>{{ t('systemConfig.title') }}</template>
    <TableShell :data="configs" storage-key="run-config">
      <el-table-column prop="key" :label="t('common.configKey')" width="200" />
      <el-table-column prop="value" :label="t('systemConfig.currentValue')" width="200">
        <template #default="{ row }">
          <span v-if="row.value_type === 'bool'">{{ row.value === 'true' ? t('systemConfig.boolOn') : t('systemConfig.boolOff') }}</span>
          <span v-else-if="row.value_type === 'password'">{{ row.has_value ? t('systemConfig.pwdShown') : t('systemConfig.pwdShownNo') }}</span>
          <span v-else-if="row.bounds?.percent">{{ pctShow(parseFloat(row.value)) }}</span>   <!-- 批45：0~1 比例键显示 90%（存储不动） -->
          <span v-else>{{ row.value }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="value_type" :label="t('common.type')" width="80" />
      <el-table-column prop="description" :label="t('risk.label')" show-overflow-tooltip>
        <template #default="{ row }">{{ descOf(row) }}</template>   <!-- 批45：词条优先（多语言），DB description 兜底 -->
      </el-table-column>
      <el-table-column prop="updated_at" :label="t('common.updatedAt')" width="220">
        <template #default="{ row }">{{ row.updated_at }}</template>
      </el-table-column>
      <el-table-column prop="actions" :label="t('common.action')" width="120">
        <template #default="{ row }">
          <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
        </template>
      </el-table-column>
    </TableShell>
    <div style="color: var(--text-secondary); font-size: var(--fs-foot); margin-top: 12px">{{ t('systemConfig.hint') }}</div>

    <!-- 批35 编辑弹窗：四型控件原样搬入（int/float 保留 :min=0 批28-7 防线）；标题不带行项名（批24 裁定）
         快审 P2-1 收紧：保存 in-flight 时 ESC/X 关窗会丢在途编辑值——before-close 守卫+取消钮禁用 -->
    <el-dialog v-model="editDlg" :close-on-click-modal="false" :before-close="guardClose" :title="t('systemConfig.editTitle')" width="480px">
      <el-form v-if="editing" label-width="90px">
        <el-form-item :label="t('common.configKey')"><span>{{ editing.key }}</span></el-form-item>
        <el-form-item :label="t('risk.label')"><span>{{ descOf(editing) }}</span></el-form-item>   <!-- 批45 代码盲审 A-P1-1：弹窗同走词条优先（原直显 DB 中文） -->
        <el-form-item :label="t('systemConfig.newValue')">
          <el-input-number v-if="editing.value_type === 'int' || editing.value_type === 'float'"
            v-model="editing.editValue"
            :min="numMin" :max="numMax" :step="numStep" :precision="numPrecision" style="width: 140px" />
          <span v-if="isPct" style="margin-left: 6px">%</span>
          <!-- 批45：percent 键换算模型（0.9↔90，步进 1%=0.01）；min/max 由注册表 bounds 驱动（批36b-α；
               batch28-7 缺省回落 0；xtp 负值禁用语义放开）；float 缺省 step 0.1 防 step=1 坑复发 -->
          <el-switch v-else-if="editing.value_type === 'bool'" v-model="editing.editValue" />
          <el-input v-else-if="editing.value_type === 'password'" v-model="editing.editValue" type="password" show-password autocomplete="new-password"
            style="width: 100%" :placeholder="editing.has_value ? t('systemConfig.pwdSet') : t('systemConfig.pwdEmpty')" />
          <el-input v-else v-model="editing.editValue" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="saving" @click="editDlg = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import TableShell from './TableShell.vue'
import IconBtn from './IconBtn.vue'
import { Edit } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getSystemConfig, updateSystemConfig, apiErr } from '../api'
import { toPct, pctShow, fromPct, camelKey } from '../utils/pct'

const { t, te } = useI18n()
const configs = ref([])
const editDlg = ref(false)
const editing = ref(null)
const saving = ref(false)
const guardClose = (done) => { if (!saving.value) done() }   // 快审 P2-1：in-flight 保存禁 ESC/X 关窗
const descOf = (row) => {   // 批45：词条优先（en 环境不再露 DB 中文），无词条回落 DB（新键兜底）
  const k = `systemConfig.desc.${camelKey(row.key)}`
  return te(k) ? t(k) : row.description
}
// 批45：percent 键换算模型（bound 也 ×100——lo 0/hi 1 → 0/100）
const isPct = computed(() => !!editing.value?.bounds?.percent)
const numMin = computed(() => {
  const b = editing.value?.bounds
  if (!b) return 0   // 批28-7 缺省回落
  return isPct.value ? b.lo * 100 : b.lo
})
const numMax = computed(() => {
  const hi = editing.value?.bounds?.hi
  if (hi == null) return undefined
  return isPct.value ? hi * 100 : hi
})
const numStep = computed(() => (isPct.value ? 1 : (editing.value?.value_type === 'float' ? 0.1 : 1)))
const numPrecision = computed(() => (isPct.value ? 0 : undefined))
const load = async () => {
  try {
    const r = await getSystemConfig()
    configs.value = (r.items || []).filter(c => !c.key.startsWith('smtp_'))
  } catch (e) { ElMessage.error(t('common.loadFailed')) }
}
const openEdit = (row) => {
  let editValue = row.value
  if (row.value_type === 'int') editValue = parseInt(row.value)
  else if (row.value_type === 'float') editValue = parseFloat(row.value)
  else if (row.value_type === 'bool') editValue = row.value === 'true'
  else if (row.value_type === 'password') editValue = ''   // 密码永远空起——留空=不改（后端空值跳过，pwdSet 占位已表达）
  if (row.bounds?.percent) editValue = toPct(editValue)   // 批45：0.9→90（save 时 fromPct 回 0~1 域）
  editing.value = { ...row, editValue }
  editDlg.value = true
}
const save = async () => {
  saving.value = true
  try {
    const outVal = editing.value.bounds?.percent ? fromPct(editing.value.editValue) : editing.value.editValue   // 批45：91→0.91
    const res = await updateSystemConfig(editing.value.key, outVal)
    if (res.dynamic) {
      const d = res.dynamic
      if (d.applied) {
        ElMessage.success(t('systemConfig.updatedDynamic', { workers: JSON.stringify(d.workers) }))
      } else {
        ElMessage.warning(t('systemConfig.updatedReason', { reason: d.reason }))
      }
    } else {
      ElMessage.success(t('systemConfig.updated'))
    }
    editDlg.value = false   // 成功才关窗；失败留在弹窗内改了重试
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}
onMounted(load)
</script>
