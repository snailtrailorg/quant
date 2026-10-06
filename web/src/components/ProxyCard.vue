<template>
  <!-- 批102a：代理配置面（全局池 + 按消费方绑定 + 测连通）。
       池=proxy_config（url 只回**掩码**值 ⇒ 编辑时 url 留空=不改，见 openEdit 注释）；
       绑定=proxy_binding（enable 与 select 两个独立选项——威廉姆原话「是否启用 / 选池中哪个」）。
       消费方集合由后端 `capable_consumers()` 派生（＝adapter 类声明了 supports_exit_config），
       故 102b 接 OKX 后本页**零改动**自动长出 okx 行——不在此硬编码 provider 名单。 -->
  <div>
    <el-card>
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">{{ t('proxy.poolTitle') }}</span>
          <IconBtn :icon="Plus" :title="t('proxy.add')" @click="openAdd" />
        </div>
      </template>
      <div class="order-note">{{ t('proxy.poolHint') }}</div>
      <TableShell :data="pool" storage-key="proxy-pool" row-key="name">
        <el-table-column prop="name" :label="t('proxy.name')" min-width="140" show-overflow-tooltip />
        <el-table-column prop="url" :label="t('proxy.url')" min-width="240" show-overflow-tooltip />
        <el-table-column :label="t('proxy.scheme')" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.local_dns" type="warning" size="small">{{ t('proxy.localDns') }}</el-tag>
            <el-tag v-else type="success" size="small">{{ t('proxy.proxyDns') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('proxy.status')" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? t('proxy.enabled') : t('proxy.disabled') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="120">
          <template #default="{ row }">
            <IconBtn size="small" :icon="Connection" :loading="probing === row.name"
                     :title="t('proxy.probe')" @click="probe(row)" />
            <IconBtn size="small" :icon="Edit" :title="t('common.edit')" @click="openEdit(row)" />
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('common.delete')" @click="del(row)" />
          </template>
        </el-table-column>
      </TableShell>
    </el-card>

    <el-card style="margin-top: 12px">
      <template #header><span style="font-weight: 600">{{ t('proxy.bindTitle') }}</span></template>
      <div class="order-note">{{ t('proxy.bindHint') }}</div>
      <TableShell :data="bindRows" storage-key="proxy-bindings" row-key="consumer">
        <el-table-column prop="consumer" :label="t('proxy.consumer')" min-width="160" show-overflow-tooltip />
        <el-table-column :label="t('proxy.enable')" width="80">
          <template #default="{ row }"><el-switch v-model="row.proxy_enabled" /></template>
        </el-table-column>
        <el-table-column :label="t('proxy.pick')" min-width="180">
          <template #default="{ row }">
            <el-select v-model="row.proxy_name" clearable filterable
                       :placeholder="t('proxy.pickPh')" style="width: 100%">
              <el-option v-for="p in pool" :key="p.name" :value="p.name" :label="p.name"
                         :disabled="!p.enabled" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('proxy.endpoint')" min-width="220">
          <template #default="{ row }">
            <el-input v-model="row.endpoint_override" :placeholder="t('proxy.endpointPh')" />
          </template>
        </el-table-column>
        <el-table-column :label="t('common.action')" width="110">
          <template #default="{ row }">
            <IconBtn size="small" :icon="Check" :title="t('common.save')" @click="saveBinding(row)" />
            <IconBtn size="small" :icon="Delete" type="danger" :title="t('proxy.clearBinding')"
                     :disabled="!row._bound" @click="clearBinding(row)" />
          </template>
        </el-table-column>
      </TableShell>
    </el-card>

    <el-dialog v-model="dlg" :close-on-click-modal="false"
               :title="isEdit ? t('proxy.editTitle') : t('proxy.addTitle')" width="560px">
      <el-form label-position="top" style="max-width: 500px">
        <el-form-item :label="t('proxy.name')">
          <el-input v-model="form.name" :disabled="isEdit" :placeholder="t('proxy.namePh')" />
        </el-form-item>
        <el-form-item :label="t('proxy.url')">
          <el-input v-model="form.url" :placeholder="urlPlaceholder" />
        </el-form-item>
        <div class="order-note">{{ t('proxy.urlHint') }}</div>
        <el-form-item :label="t('proxy.notes')" style="margin-top: 12px">
          <el-input v-model="form.notes" :placeholder="t('proxy.notesPh')" />
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
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProxies, upsertProxy, deleteProxy, listProxyBindings, setProxyBinding,
         deleteProxyBinding, probeProxy, apiErr } from '../api'
import TableShell from './TableShell.vue'
import IconBtn from './IconBtn.vue'
import { Edit, Delete, Plus, Check, Connection } from '@element-plus/icons-vue'

const { t } = useI18n()
const pool = ref([])
const bindRows = ref([])
const dlg = ref(false)
const isEdit = ref(false)
const saving = ref(false)
const probing = ref('')
const form = ref({ name: '', url: '', notes: '', enabled: true })

// 编辑时 url 只给**掩码**值（后端 list_proxies 掩码）⇒ 不可回填输入框，否则提交会把
// `socks5h://***@h:1080` 原样写回、静默毁掉凭证。空=不改（与 SMTP 密码同款语义）。
const urlPlaceholder = computed(() => (isEdit.value
  ? t('proxy.urlKeepPh', { url: currentMasked.value })
  : t('proxy.urlPh')))
const currentMasked = ref('')

const load = async () => {
  try {
    const r = await listProxies()
    pool.value = r.items || []
    const b = (await listProxyBindings()).items || []
    const by = Object.fromEntries(b.map(x => [x.consumer, x]))
    // 行集合＝可配消费方（后端派生）。绑定了但已不可配的消费方也列出（防「配了却看不见」）
    const consumers = [...new Set([...(r.capable_consumers || []), ...b.map(x => x.consumer)])].sort()
    bindRows.value = consumers.map(c => ({
      consumer: c,
      proxy_enabled: !!by[c]?.proxy_enabled,
      proxy_name: by[c]?.proxy_name ?? null,
      endpoint_override: by[c]?.endpoint_override ?? null,
      _bound: !!by[c],
    }))
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
}

const openAdd = () => {
  isEdit.value = false
  currentMasked.value = ''
  form.value = { name: '', url: '', notes: '', enabled: true }
  dlg.value = true
}
const openEdit = (row) => {
  isEdit.value = true
  currentMasked.value = row.url
  form.value = { name: row.name, url: '', notes: row.notes || '', enabled: row.enabled }
  dlg.value = true
}

const save = async () => {
  saving.value = true
  try {
    await upsertProxy({ name: form.value.name, url: form.value.url, notes: form.value.notes,
                        enabled: form.value.enabled })
    ElMessage.success(t('common.saveSuccess'))
    dlg.value = false
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
  finally { saving.value = false }
}

const del = async (row) => {
  try {
    await ElMessageBox.confirm(t('proxy.delConfirm', { name: row.name }),
                               t('common.delete'), { type: 'warning' })
    await deleteProxy(row.name)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.detail || e?.code) ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

const probe = async (row) => {
  probing.value = row.name
  try {
    const r = await probeProxy(row.name)
    if (r.ok) ElMessage.success(t('proxy.probeOk', { ip: r.exit_ip || '?', ms: r.elapsed_ms ?? '?' }))
    else ElMessage.error(t('proxy.probeFail', { err: r.error || '?' }))
  } catch (e) { ElMessage.error(apiErr(e, t('common.operationFailed'))) }
  finally { probing.value = '' }
}

const saveBinding = async (row) => {
  if (row.proxy_enabled && !row.proxy_name) {
    ElMessage.warning(t('proxy.needProxy')); return
  }
  try {
    await setProxyBinding(row.consumer, {
      proxy_enabled: row.proxy_enabled,
      proxy_name: row.proxy_name,
      endpoint_override: row.endpoint_override || null,
    })
    ElMessage.success(t('common.saveSuccess'))
    await load()
  } catch (e) { ElMessage.error(apiErr(e, t('common.saveFailed'))) }
}

const clearBinding = async (row) => {
  try {
    await ElMessageBox.confirm(t('proxy.clearConfirm', { consumer: row.consumer }),
                               t('proxy.clearBinding'), { type: 'warning' })
    await deleteProxyBinding(row.consumer)
    ElMessage.success(t('common.deleteSuccess'))
    await load()
  } catch (e) { if (e?.detail || e?.code) ElMessage.error(apiErr(e, t('common.deleteFailed'))) }
}

onMounted(load)
</script>

<style scoped>
/* 与 SmtpCard 同款说明行（复用既有形态，不新增令牌面） */
.order-note { font-size: var(--fs-foot); color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }
</style>
