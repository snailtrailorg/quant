<template>
  <!-- 批16 列显示配置（盲审 A-P0：数据中心两表 11 列 ≈1570px 溢出——用户可按需隐藏列；
       新列默认隐，配置持久化 localStorage，键=storageKey）。
       批17 17C：触发器图标化（Setting 圆钮——工具位统一图标形态，用户裁定）。
       批23：图标 Filter→Operation（用户裁定：Filter 让位行筛选 RowFilter，列设置用扳手形）。
       批126h 重建（用户裁定：对齐 RowFilter 菜单标准）——①激活蓝点：visible≠默认列集时亮
       （列配置偏离默认=有状态，与行筛选「有筛选」同语义；7px/--dot/brand-500 同款）；
       ②「恢复默认（N）」顶部常驻+disabled（已在默认态灰置——popover 高度稳定，批126c
       同裁定）；③全菜单 fs-label 统一字号（EP checkbox label 默认 14px 须 :deep 覆盖）；
       ④popover 240→260 对齐。语义差异保留（不照搬 RowFilter 两点）：动作词=「恢复默认」
       非「清除筛选」（列显隐无「空=全显」契约——空档=全隐藏是有效用户选择，P2-5 立法）；
       disabled 判据=已在默认态（非无勾选）。 -->
  <el-popover :width="260" trigger="click">
    <template #reference>
      <!-- 偏离默认指示：蓝点（visible≠默认列集）——同 RowFilter 激活点 -->
      <span class="cs-ref">
        <IconBtn :icon="Operation" :title="t('cols.settings')" />
        <span v-if="offCount > 0" class="cs-dot" />
      </span>
    </template>
    <!-- 恢复默认：顶部常驻+disabled（批126h——原底部 el-button link 蓝链样式退役） -->
    <button type="button" class="cs-reset" :disabled="offCount === 0" @click="resetDefault">
      {{ t('cols.reset') }}（{{ offCount }}）
    </button>
    <el-divider style="margin: var(--sp-1) 0" />
    <div style="max-height: 320px; overflow-y: auto">
      <el-checkbox-group v-model="visible">
        <el-checkbox v-for="c in columns" :key="c.key" :value="c.key" class="col-opt">{{ c.label }}</el-checkbox>
      </el-checkbox-group>
    </div>
  </el-popover>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Operation } from '@element-plus/icons-vue'
import IconBtn from './IconBtn.vue'
import { colOffCount } from '../utils/colDiff'

const props = defineProps({
  storageKey: { type: String, required: true },
  /** 列定义 [{key, label, hidden?}]——hidden=true 的列默认不显示；不进名单的列（名称/操作）恒显不可配 */
  columns: { type: Array, required: true },
})
const visible = defineModel('visible', { type: Array, default: () => [] })

const { t } = useI18n()
const defaultKeys = computed(() => props.columns.filter(c => !c.hidden).map(c => c.key))

// 与默认列集的对称差数（隐藏了 N 列或开出了 N 个默认隐藏列都算偏离；0=默认态）——
// 判定核心抽 utils/colDiff.js 纯函数（批126h 单测通道：web/scripts/test-coldiff.mjs）
const offCount = computed(() => colOffCount(defaultKeys.value, visible.value))

// 初始化：localStorage 优先（剔掉已下线的键），无存档用默认。
// 盲审A-P2-5：全隐藏（空档）也是有效用户选择要持久化——"空档≠无档"；
// 存档键全部下线（schema 变更）才回落默认。
const stored = (() => {
  try { return JSON.parse(localStorage.getItem(props.storageKey) || '') } catch { return null }
})()
if (Array.isArray(stored)) {
  const valid = stored.filter(k => props.columns.some(c => c.key === k))
  if (valid.length > 0 || stored.length === 0) visible.value = valid
  else visible.value = defaultKeys.value
} else {
  visible.value = defaultKeys.value
}

watch(visible, v => localStorage.setItem(props.storageKey, JSON.stringify(v)), { deep: true })

const resetDefault = () => { visible.value = defaultKeys.value }
</script>

<style scoped>
/* 批126h：与 RowFilter 同标准（rf-opt 同款）——全菜单 fs-label 统一，EP checkbox
   label 默认 --el-font-size-base(14px) 须 :deep 覆盖 */
.col-opt { display: block; height: 26px; line-height: 26px; margin-right: 0; width: 100%;
  font-size: var(--fs-label); font-weight: 400; }
:deep(.col-opt .el-checkbox__label) { font-size: var(--fs-label); font-weight: 400; }
/* 偏离默认指示：蓝点贴图标右上角（同 rf-dot——--dot/--brand-500 tokens） */
.cs-ref { position: relative; display: inline-flex; }
.cs-dot { position: absolute; top: 0; right: 0; width: var(--dot); height: var(--dot);
  border-radius: 50%; background: var(--brand-500); }
/* 恢复默认：rf-clear 同款（fs-label/600/text-primary）；disabled=已在默认态；
     padding 走 tokens（令牌门禁新增内联 px） */
.cs-reset { width: 100%; display: block; text-align: left;
  padding: var(--sp-1) var(--sp-2);
  border: none; background: none; cursor: pointer;
  font: inherit; font-size: var(--fs-label); font-weight: 600; color: var(--text-primary); }
.cs-reset:disabled { color: var(--text-secondary); opacity: .5; cursor: default; }
</style>
