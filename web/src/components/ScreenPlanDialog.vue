<template>
  <el-dialog :model-value="visible" :title="dlgTitle" width="720px" :close-on-click-modal="false"
             @update:model-value="v => emit('close', v)">
    <el-tabs v-model="tab">
      <el-tab-pane :label="t('screener.tabBasic')" name="basic">
        <div v-if="!fields.length" class="hint">{{ t('screener.noFields') }}</div>
        <ScreenConditionRow v-for="(c, i) in conds" :key="c._k" :row="c" :fields="fields"
                            @upd="p => Object.assign(conds[i], p)" @del="conds.splice(i, 1)" />
        <el-button size="small" class="blk" :disabled="!fields.length" @click="addCond">+ {{ t('screener.addCond') }}</el-button>
        <div class="hint" style="margin-top: var(--sp-2)">{{ t('screener.orHint') }}</div>
      </el-tab-pane>
      <el-tab-pane :label="t('screener.tabAdvanced')" name="dsl">
        <el-input v-model="dslFilter" type="textarea" :rows="3" class="blk" :placeholder="t('screener.dslFilterPh')" />
        <el-input v-model="dslRank" type="textarea" :rows="2" class="blk" :placeholder="t('screener.dslRankPh')" />
        <div v-if="dslErr" class="err">{{ dslErr }}</div>
      </el-tab-pane>
    </el-tabs>
    <template #footer>
      <el-button @click="emit('close', false)">{{ t('common.cancel') }}</el-button>
      <el-button :loading="saving" @click="save(false)">{{ t('common.save') }}</el-button>
      <el-button :disabled="!canRun" @click="applyOnly">{{ t('screener.applyBtn') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="!canRun" @click="save(true)">{{ t('screener.saveApply') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'
import ScreenConditionRow from './ScreenConditionRow.vue'

const props = defineProps({
  visible: Boolean, category: { type: String, required: true },
  plan: { type: Object, default: null },   // null=新建
  fields: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'saved', 'applydef'])
const { t, te } = useI18n()

const tab = ref('basic')
let seq = 0
const conds = ref([])
const dslFilter = ref('')
const dslRank = ref('')
const dslErr = ref('')
const saving = ref(false)

const dlgTitle = computed(() => props.plan ? t('screener.editPlan') + '：' + props.plan.name : t('screener.newPlan'))

watch(() => props.visible, (v) => {
  if (!v) return
  dslErr.value = ''
  const d = props.plan?.definition || {}
  if (d.filter !== undefined) {
    tab.value = 'dsl'
    dslFilter.value = d.filter || ''
    dslRank.value = d.rank || ''
    conds.value = []
  } else {
    tab.value = 'basic'
    dslFilter.value = ''; dslRank.value = ''
    conds.value = (d.rows || []).map(r => ({ ...r, _k: ++seq }))
    if (!conds.value.length) addCond()
  }
})

const addCond = () => {
  const first = props.fields.find(f => !f.dead)
  if (!first) return
  conds.value.push({ _k: ++seq, field: first.name, op: first.type === 'bool' ? '=' : '<',
                     value: first.type === 'bool' ? true : null })
}
const typeOf = f => (props.fields.find(x => x.name === f) || {}).type
const liveConds = () => conds.value.filter(c => c.field && c.value !== null && c.value !== undefined
  && !(typeOf(c.field) === 'bool' && c.value !== true))
const curDefinition = () => tab.value === 'dsl'
  ? { v: 1, filter: dslFilter.value, rank: dslRank.value || undefined }
  : { v: 1, rows: liveConds().map(c => ({ field: c.field, op: c.op, value: c.value })) }
const canRun = computed(() => tab.value === 'dsl'
  ? !!dslFilter.value.trim()
  : liveConds().length > 0)

const applyOnly = () => { emit('applydef', curDefinition()); emit('close', false) }

const save = async (thenApply) => {
  const def = curDefinition()
  if (def.filter !== undefined && !def.filter.trim()) { ElMessage.warning(t('screener.needCond')); return }
  saving.value = true
  try {
    if (props.plan) {
      await api.patch(`/screen/presets/${props.plan.id}`, { name: props.plan.name, category: props.category, definition: def })
    } else {
      try {
        const { value: name } = await ElMessageBox.prompt(t('screener.planName'), t('screener.newPlan'))
        await api.post('/screen/presets', { name, category: props.category, definition: def })
      } catch (e) { if (e === 'cancel' || e === 'close') return; throw e }
    }
    if (thenApply) { emit('applydef', def); emit('close', false) }
    else { ElMessage.success(t('common.success')); emit('saved') }
  } catch (e) {
    const code = e?.response?.data?.code || e?.code
    if (code && te('err.' + code)) ElMessage.error(t('err.' + code))
    else ElMessage.error(apiErr(e, t('common.saveFailed')))
  } finally { saving.value = false }
}
</script>

<style scoped>
.blk { width: 100%; margin-bottom: var(--sp-1); }
.hint { font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-1); }
.err { font-size: var(--fs-foot); color: var(--critical); margin-bottom: var(--sp-1); }
</style>
