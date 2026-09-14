<template>
  <!-- 批21：全站圆形图标按钮统一组件（用户裁定：统一风格，尺寸唯一源=--icon-btn 令牌）。
       配色规则 v2（用户裁定「正式沉稳不失活力」）：图标一律 ghost 风格——
       透明底 + 彩色图标（默认品牌蓝 --brand-600）+ hover 淡中性底；颜色只表语义：
       type=''（品牌蓝，常规）| danger 红 | warning 橙 | success 绿。
       默认 inheritAttrs（单根透传，ColumnSettings 的 el-popover #reference click 依赖此透传，勿设 inheritAttrs:false）。
       title 存在时自动补 aria-label（无障碍）。 -->
  <el-button
    circle
    :title="title || undefined"
    :disabled="disabled"
    :loading="loading"
    :aria-label="ariaLabel || title || undefined"
    class="icon-btn"
    :class="[type ? `icon-btn--${type}` : '', size === 'small' ? 'icon-btn--small' : '']"
    @click="$emit('click', $event)"
  >
    <el-icon v-if="icon && !loading"><component :is="icon" /></el-icon>
    <slot v-else />
  </el-button>
</template>

<script setup>
defineProps({
  icon: { type: Object, default: null },   // EP 图标组件
  title: { type: String, default: '' },
  type: { type: String, default: '' },     // 语义色 tone：''（品牌蓝）| danger | warning | success
  size: { type: String, default: '' },     // ''（32px，标题栏/工具栏）| small（24px，行内操作）
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' },
})
defineEmits(['click'])
</script>

<style scoped>
.icon-btn {
  width: var(--icon-btn);
  height: var(--icon-btn);
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--brand-600);          /* 常规：品牌蓝图标（有活力，不灰白） */
  background: var(--el-fill-color-light);   /* 浅蓝灰底：圆钮轮廓可见，沉稳不张扬 */
  border: none;
}
.icon-btn:hover:not(.is-disabled) { background: var(--el-color-primary-light-8); }   /* hover 浅蓝，有活力 */
/* 行内操作小号（24px，表格操作列密度）：尺寸与字形同缩 */
.icon-btn--small { width: 24px; height: 24px; }
.icon-btn--small :deep(.el-icon) { font-size: 14px; }
/* 图标字形尺寸：el-icon 按 1em 走，这里放大；文字槽位（语言「EN/中」）不受影响 */
.icon-btn :deep(.el-icon) { font-size: var(--icon-glyph); }
/* 语义色：颜色在图标，底仍透明，hover 同淡中性 */
.icon-btn--danger { color: var(--el-color-danger); }
.icon-btn--warning { color: var(--el-color-warning); }
.icon-btn--success { color: var(--el-color-success); }
</style>
