<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('pool.manageTitle') }}</span>
        <el-button type="primary" @click="showDialog = true">{{ t('pool.createTitle') }}</el-button>
      </div>
    </template>
    <el-table :data="pools" stripe>
      <el-table-column prop="id" label="ID" width="150" />
      <el-table-column prop="name" :label="t('common.name')" width="200" />
      <el-table-column prop="category" :label="t('pool.category')" width="120" />
      <el-table-column :label="t('pool.symbolCount')" width="100">
        <template #default="{ row }">{{ row.symbols?.length || 0 }}</template>
      </el-table-column>
      <el-table-column prop="description" :label="t('common.description')" />
      <el-table-column :label="t('common.action')" width="150">
        <template #default="{ row }">
          <el-button type="primary" @click="editPool(row)">{{ t('common.edit') }}</el-button>
          <el-button type="danger" @click="delPool(row)">{{ t('common.delete') }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showDialog" :title="t('pool.createTitle')" width="500px">
      <el-form :model="newPool" label-width="80px">
        <el-form-item label="ID"><el-input v-model="newPool.id" /></el-form-item>
        <el-form-item :label="t('common.name')"><el-input v-model="newPool.name" /></el-form-item>
        <el-form-item :label="t('pool.category')">
          <el-select v-model="newPool.category" style="width: 100%">
            <el-option :label="t('pool.catAstock')" value="astock" />
            <el-option :label="t('pool.catConvertible')" value="convertible" />
            <el-option :label="t('pool.catEtf')" value="etf" />
            <el-option :label="t('pool.catCrypto')" value="crypto" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('pool.symbolList')">
          <el-input v-model="newPool.symbolsStr" type="textarea" :rows="4" :placeholder="t('pool.phSymbols')" />
        </el-form-item>
        <el-form-item :label="t('common.description')"><el-input v-model="newPool.description" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="showDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="createPool">{{ t('pool.createBtn') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getPools, createPoolApi, deletePoolApi } from '../api'

const { t } = useI18n()
const pools = ref([])
const showDialog = ref(false)
const newPool = ref({ id: '', name: '', category: 'astock', symbolsStr: '', description: '' })

const load = async () => {
  try { pools.value = await getPools() } catch (e) { ElMessage.error(t('pool.loadFailed')) }
}
const createPool = async () => {
  if (!newPool.value.id || !newPool.value.name) { ElMessage.warning(t('pool.idNameRequired')); return }
  try {
    await createPoolApi(newPool.value)
    ElMessage.success(t('common.saveSuccess'))
    showDialog.value = false
    newPool.value = { id: '', name: '', category: 'astock', symbolsStr: '', description: '' }
    await load()
  } catch (e) { ElMessage.error(t('common.saveFailed')) }
}
const editPool = row => {
  newPool.value = { id: row.id, name: row.name, category: row.category || 'astock', symbolsStr: (row.symbols || []).join('\n'), description: row.description || '' }
  showDialog.value = true
}
const delPool = async row => {
  try { await deletePoolApi(row.id); ElMessage.success(t('common.deleteSuccess')); await load() } catch (e) { ElMessage.error(t('common.deleteFailed')) }
}
onMounted(load)
</script>
