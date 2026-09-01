<template>
  <el-card>
    <template #header>{{ t('riskRule.title') }}</template>
    <el-table :data="rules">
      <el-table-column prop="name" :label="t('common.name')" show-overflow-tooltip />
      <el-table-column prop="type" :label="t('common.type')" width="160" />
      <el-table-column prop="params" :label="t('riskRule.params')" show-overflow-tooltip />
      <el-table-column :label="t('common.enable')" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column :label="t('common.action')" width="180">
        <template #default="{ row }">
          <el-button type="primary" @click="onEdit(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="onDelete(row.id)" :disabled="navReadonly">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? t('riskRule.editRule') : t('riskRule.addRule') }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item :label="t('common.name')"><el-input v-model="form.name" /></el-form-item>
      <el-form-item :label="t('common.type')">
        <el-select v-model="form.type" style="width: 220px">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('riskRule.params')">
        <el-input v-model="form.params" :placeholder="t('riskRule.phParams')" style="width:320px" />
      </el-form-item>
      <el-form-item :label="t('common.enable')"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving" :disabled="navReadonly">{{ form.id ? t('common.update') : t('riskRule.add') }}</el-button>
        <el-button type="primary" @click="resetForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import {apiErr,  getRiskRules, getRiskRuleTypes, createRiskRule, updateRiskRule, deleteRiskRule } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const { t } = useI18n()
const navReadonly = inject('navReadonly', ref(false))
const rules = ref([])
const types = ref([])
const form = ref(emptyForm())
const saving = ref(false)

function emptyForm() {
  return { name: '', type: 'max_position', params: '{}', enabled: true }
}

const load = async () => {
  try {
    rules.value = await getRiskRules()
    types.value = (await getRiskRuleTypes()).types || []
  } catch (e) { console.error(e) }
}
onMounted(load)

const onEdit = (row) => { form.value = { ...row } }
const resetForm = () => { form.value = emptyForm() }

const onSave = async () => {
  saving.value = true
  try {
    if (form.value.id) await updateRiskRule(form.value.id, form.value)
    else await createRiskRule(form.value)
    ElMessage.success(t('common.saveSuccess'))
    resetForm()
    load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm(t('riskRule.confirmDelete'), t('common.tip'), { type: 'warning' })
  await deleteRiskRule(id)
  ElMessage.success(t('common.deleteSuccess'))
  load()
}
</script>
