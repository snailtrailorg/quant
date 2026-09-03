<script setup>
// wd-20 §2.6 · EmptyState 约定组件（el-empty + 主操作钮）——替代全站裸灰字空态
// 表格空态用 el-table 自带 empty slot（不套本组件——规则见 DESIGN.md）
const props = defineProps({
  description: { type: String, default: '' },
  actionLabel: { type: String, default: '' },   // 主操作钮文案（空=不显示）
  size: { type: String, default: 'default' },   // default | small
})
const emit = defineEmits(['action'])
</script>
<template>
  <el-empty :description="description || undefined" :image-size="size === 'small' ? 48 : 80">
    <el-button v-if="actionLabel" type="primary" size="small" @click="emit('action')">{{ actionLabel }}</el-button>
  </el-empty>
</template>
