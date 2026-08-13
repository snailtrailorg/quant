<template>
  <el-card>
    <template #header>风控规则管理（平台化风控，规则 DB 化 + 可扩展）</template>
    <el-table :data="rules" stripe size="small">
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="type" label="类型" width="160" />
      <el-table-column prop="params" label="参数(JSON)" />
      <el-table-column label="启用" width="80">
        <template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '✓' : '✗' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-divider />
    <h3 style="font-size: 16px; margin-bottom: 12px">{{ form.id ? '编辑规则' : '添加规则' }}</h3>
    <el-form :model="form" label-width="100px" inline>
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="类型">
        <el-select v-model="form.type" style="width: 220px">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
      </el-form-item>
      <el-form-item label="参数(JSON)">
        <el-input v-model="form.params" placeholder='{"max_pct":0.1} 或 {"max_amount":100000}' style="width:320px" />
      </el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      <el-form-item>
        <el-button type="primary" @click="onSave" :loading="saving">{{ form.id ? '更新' : '添加' }}</el-button>
        <el-button @click="resetForm">重置</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getRiskRules, getRiskRuleTypes, createRiskRule, updateRiskRule, deleteRiskRule } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

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
    ElMessage.success('保存成功')
    resetForm()
    load()
  } catch (e) { ElMessage.error(e.detail || '保存失败') }
  finally { saving.value = false }
}

const onDelete = async (id) => {
  await ElMessageBox.confirm('确认删除此规则？', '提示', { type: 'warning' })
  await deleteRiskRule(id)
  ElMessage.success('已删除')
  load()
}
</script>
