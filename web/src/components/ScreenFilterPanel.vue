<template>
  <div>
    <el-tabs v-model="tab">
      <el-tab-pane :label="t('screener.tabBasic')" name="basic">
        <el-radio-group v-model="match" size="small" class="blk">
          <el-radio-button value="all">{{ t('screener.matchAll') }}</el-radio-button>
          <el-radio-button value="any">{{ t('screener.matchAny') }}</el-radio-button>
        </el-radio-group>
        <div v-if="!fields.length" class="hint">{{ t('screener.noFields') }}</div>
        <ScreenConditionRow v-for="c in conds" :key="c._k" :row="c" :fields="fields"
                            @upd="p => Object.assign(c, p)" @del="removeCond(c)" />
        <el-button size="small" class="blk" @click="addCond">+ {{ t('screener.addCond') }}</el-button>
        <el-button type="primary" class="blk" :loading="loading" :disabled="!fields.length"
                   @click="run">{{ t('screener.run') }}</el-button>
      </el-tab-pane>
      <el-tab-pane :label="t('screener.tabAdvanced')" name="dsl">
        <el-input v-model="dslFilter" type="textarea" :rows="3" size="small" class="blk"
                  :placeholder="t('screener.dslFilterPh')" />
        <el-input v-model="dslRank" type="textarea" :rows="2" size="small" class="blk"
                  :placeholder="t('screener.dslRankPh')" />
        <div v-if="dslErr" class="err">{{ dslErr }}</div>
        <el-button type="primary" class="blk" :loading="loading" :disabled="!fields.length"
                   @click="runDsl">{{ t('screener.run') }}</el-button>
      </el-tab-pane>
    </el-tabs>
    <el-divider />
    <div class="hint">{{ t('screener.savedPlans') }}</div>
    <div v-for="p in presets" :key="p.id" class="preset-row">
      <el-button size="small" text type="primary" class="preset-name" @click="applyPreset(p)">{{ p.name }}</el-button>
      <el-tag v-if="p.source === 'builtin'" size="small" type="info">{{ t('screener.builtinTag') }}</el-tag>
      <el-button v-if="p.source !== 'builtin'" size="small" text @click="delPreset(p)">×</el-button>
    </div>
    <el-button size="small" class="blk" :disabled="!fields.length" @click="savePreset">{{ t('screener.savePlan') }}</el-button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import api, { apiErr } from '../api'
import ScreenConditionRow from './ScreenConditionRow.vue'

const props = defineProps({ category: { type: String, required: true } })
const emit = defineEmits(['result'])
const { t, te } = useI18n()

const tab = ref('basic')
const match = ref('all')
let seq = 0
const conds = ref([])
const fields = ref([])
const presets = ref([])
const dslFilter = ref('')
const dslRank = ref('')
const dslErr = ref('')
const loading = ref(false)

const addCond = () => {
  const first = fields.value[0]
  if (!first) return
  conds.value.push({ _k: ++seq, field: first.name, op: first.type === 'bool' ? '=' : '<',
                     value: first.type === 'bool' ? true : null })
}
const removeCond = (c) => { conds.value = conds.value.filter(x => x !== c) }

const loadFields = async () => {
  try {
    fields.value = (await api.get(`/screen/fields?category=${props.category}`))?.fields || []
    if (fields.value.length && !conds.value.length) addCond()
  } catch { fields.value = [] }
}
const loadPresets = async () => {
  try { presets.value = await api.get(`/screen/presets?category=${props.category}`) || [] } catch { presets.value = [] }
}
onMounted(() => { loadFields(); loadPresets() })

const typeOf = f => (fields.value.find(x => x.name === f) || {}).type
const run = async () => {
  const live = conds.value.filter(c => c.field && c.value !== null && c.value !== undefined
    && !(typeOf(c.field) === 'bool' && c.value !== true))
  if (!live.length) { ElMessage.warning(t('screener.needCond')); return }
  loading.value = true
  try {
    const j = await api.post('/screen/run', {
      category: props.category, limit: 500,
      definition: { v: 1, match: match.value, rows: live.map(c => ({ field: c.field, op: c.op, value: c.value })) },
    })
    emit('result', j.rows || [])
    if (j.nulls_excluded) ElMessage.info(t('screener.nullsExcluded', { n: j.nulls_excluded }))
  } catch (e) {
    ElMessage.error(apiErr(e, t('common.loadFailed')))
  } finally { loading.value = false }
}
const runDsl = async () => {
  if (!dslFilter.value.trim()) { ElMessage.warning(t('screener.needCond')); return }
  loading.value = true
  dslErr.value = ''
  try {
    const j = await api.post('/screen/run-dsl', {
      category: props.category, limit: 500,
      filter: dslFilter.value, rank: dslRank.value || null,
    })
    emit('result', j.rows || [])
  } catch (e) {
    const code = e?.response?.data?.code || e?.code
    dslErr.value = code && te('err.' + code) ? t('err.' + code) : apiErr(e, t('common.loadFailed'))
  } finally { loading.value = false }
}
const applyPreset = (p) => {
  const d = p.definition || {}
  if (d.filter !== undefined) {
    tab.value = 'dsl'
    dslFilter.value = d.filter || ''
    dslRank.value = d.rank || ''
    dslErr.value = ''
  } else {
    tab.value = 'basic'
    if (d.match) match.value = d.match
    if (Array.isArray(d.rows)) conds.value = d.rows.map(r => ({ ...r, _k: ++seq }))
  }
  if (p.skipped_fields?.length) ElMessage.warning(t('screener.skippedFields', { n: p.skipped_fields.length }))
}
const savePreset = async () => {
  try {
    const { value: name } = await ElMessageBox.prompt(t('screener.planName'), t('screener.savePlan'))
    const definition = tab.value === 'dsl'
      ? { v: 1, filter: dslFilter.value, rank: dslRank.value || undefined }
      : { v: 1, match: match.value, rows: conds.value.map(c => ({ field: c.field, op: c.op, value: c.value })) }
    await api.post('/screen/presets', { name, category: props.category, definition })
    await loadPresets()
    ElMessage.success(t('common.success'))
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(apiErr(e, t('common.saveFailed')))
  }
}
const delPreset = async (p) => {
  try {
    await api.delete(`/screen/presets/${p.id}`)
    await loadPresets()
  } catch (e) { ElMessage.error(apiErr(e, t('common.loadFailed'))) }
}
</script>

<style scoped>
.blk { width: 100%; margin-bottom: var(--sp-1); }
.hint { font-size: var(--fs-foot); color: var(--text-secondary); margin-bottom: var(--sp-2); }
.err { font-size: var(--fs-foot); color: var(--critical); margin-bottom: var(--sp-1); }
.preset-row { display: flex; gap: var(--sp-1); margin-bottom: var(--sp-1); align-items: center; }
.preset-name { flex: 1; justify-content: flex-start; }
</style>
