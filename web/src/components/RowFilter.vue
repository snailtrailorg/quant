<template>
  <!-- 批23：行筛选统一组件（Filter 图标钮+popover 复选组多选）。
       批24 迭代十六：分组模式——groups=[{key,label,options}]（一钮多列筛选，下拉按列分组显示），
       modelValue=对象 {key: [选中值]}；「全部」=空对象。单组模式（options）批23 语义不变。
       动态选项（module/actor 等非固定枚举）由调用方从数据 distinct 预取后传入。
       语义契约：空 modelValue=全显（5 消费页一致）；激活指示=图标角蓝点（批126c 用户裁定：
       红点违语义法——红=故障/绿=无故障，筛选激活非故障态，改 brand-500 蓝=选中/激活；
       尺寸 var(--dot)=7px 全站唯一源）；顶部「清除筛选」**常驻**+无筛选时 disabled
       （批126c 用户裁定：v-if 显隐致 popover 高度跳变，disable 保排布稳定——
       回原「全部」顶部位置）；文字样式对齐选项行（fs-foot——同 popover 视觉一致）；
       与 ColumnSettings（Operation=列显隐）成对：本组件管"行"（数据维度筛选），
       放 header 右侧动作区（统一序：筛选→…→列设置）。 -->
  <el-popover :width="260" trigger="click">
    <template #reference>
      <!-- 筛选激活指示：蓝点（有任一勾选）——尺寸/色均走 tokens（批126c） -->
      <span class="rf-ref" :class="{ active: selCount > 0 }">
        <IconBtn :icon="Filter" :title="title" />
        <span v-if="selCount > 0" class="rf-dot" />
      </span>
    </template>
    <!-- 清除筛选：顶部常驻（原「全部」位）+disabled 防跳变（批126c 用户裁定） -->
    <button type="button" class="rf-clear" :disabled="!selCount" @click="onClear">
      {{ t('common.clearFilter') }}（{{ selCount }}）
    </button>
    <el-divider style="margin: var(--sp-1) 0" />
    <div style="max-height: 300px; overflow-y: auto">
      <!-- 单组模式（批23 兼容） -->
      <el-checkbox-group v-if="!groups" :model-value="modelValue" @update:model-value="v => emit('update:modelValue', v)">
        <el-checkbox v-for="o in options" :key="o.value" :value="o.value" class="rf-opt">{{ o.label }}</el-checkbox>
      </el-checkbox-group>
      <!-- 分组模式（迭代十六） -->
      <template v-else>
        <div v-for="g in groups" :key="g.key">
          <div class="rf-group-label">{{ g.label }}</div>
          <el-checkbox-group :model-value="modelValue[g.key] || []" @update:model-value="v => onGroup(g.key, v)">
            <el-checkbox v-for="o in g.options" :key="o.value" :value="o.value" class="rf-opt">{{ o.label }}</el-checkbox>
          </el-checkbox-group>
        </div>
      </template>
    </div>
  </el-popover>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Filter } from '@element-plus/icons-vue'
import IconBtn from './IconBtn.vue'

const emit = defineEmits(['update:modelValue'])

const props = defineProps({
  /** 单组模式筛选项 [{value, label}]（批23 兼容；与 groups 二选一） */
  options: { type: Array, default: () => [] },
  /** 分组模式 [{key, label, options}]——key=数据字段/modelValue 对象键（迭代十六） */
  groups: { type: Array, default: null },
  /** 钮 tooltip（常规传 t('common.filter')） */
  title: { type: String, default: '' },
})
// 单组=modelValue=[值]；分组=modelValue={key:[值]}
const modelValue = defineModel({ type: [Array, Object], default: () => [] })

const { t } = useI18n()

const selCount = computed(() => props.groups
  ? Object.values(modelValue.value || {}).reduce((n, arr) => n + (Array.isArray(arr) ? arr.length : 0), 0)
  : modelValue.value.length)

// 清除筛选（单组 []；分组 {}）——原「全部」复选框的文字钮化（批 125）；
// 空=全显契约不变（5 消费页零改动）；popover 保持开（清完常要立刻选新项）
const onClear = () => emit('update:modelValue', props.groups ? {} : [])
const onGroup = (key, vals) => {
  emit('update:modelValue', { ...modelValue.value, [key]: vals })
}

</script>

<style scoped>
/* 批126e 用户裁定：popover 菜单内**统一字体**——全菜单一个字号（fs-label=「严重级别」
   列头同款），层级只靠字重（分组标题 600 粗体/选项与清除钮常规）。EP checkbox
   label 默认 --el-font-size-base(14px) 须显式覆盖归一 */
.rf-opt { display: block; height: 26px; line-height: 26px; margin-right: 0; width: 100%;
  font-size: var(--fs-label); font-weight: 400; }
:deep(.rf-opt .el-checkbox__label) { font-size: var(--fs-label); font-weight: 400; }
/* 激活指示：包裹层相对定位，蓝点贴图标右上角（尺寸 var(--dot)=7px 全站标准，
     色 brand-500=选中/激活语义——批126c 用户裁定，红留给故障态） */
.rf-ref { position: relative; display: inline-flex; }
.rf-dot { position: absolute; top: 0; right: 0; width: var(--dot); height: var(--dot);
  border-radius: 50%; background: var(--brand-500); }
/* 清除筛选：同字号+粗体（fs-label/600——批126e 用户裁定「清除钮 600 粗」）；
     色回归 text-secondary 灰（批126j 用户裁定：黑白对比后原灰更好——菜单动作钮
     不与正文抢重；disabled 态再降透明度）；常驻保 popover 高度稳定（批126c） */
.rf-clear { width: 100%; display: block; text-align: left; padding: 2px 8px;
  border: none; background: none; cursor: pointer;
  font: inherit; font-size: var(--fs-label); font-weight: 600; color: var(--text-secondary); }
.rf-clear:disabled { opacity: .5; cursor: default; }
/* 分组标签（一级菜单）：同字号+粗体区分层级（批126e）；灰色回归（批126j 用户裁定
   ——126g 的 text-primary 黑被否，一级菜单灰+粗体已够层级） */
.rf-group-label { font-size: var(--fs-label); font-weight: 600; color: var(--text-secondary); padding: var(--sp-1) 8px 2px; }
</style>
