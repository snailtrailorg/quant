<template>
  <el-card>
    <template v-if="title || $slots.actions" #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-weight: 600">{{ title }}</span>
        <div v-if="$slots.actions" style="display: flex; gap: var(--icon-gap); align-items: center">
          <slot name="actions" />
        </div>
      </div>
    </template>
    <slot />
  </el-card>
</template>

<script setup>
defineProps({ title: { type: String, default: '' } })
</script>
