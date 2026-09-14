<template>
  <!-- 批17 17C：刷新按钮图标化（用户裁定：通用工具位统一图标形态）——全站替换 <el-button ...>{{ t('common.refresh') }}</el-button>
       批21：改走 IconBtn，全站圆钮尺寸统一（--icon-btn） -->
  <IconBtn :icon="Refresh" :title="t('common.refresh')" :loading="loading" @click="$emit('refresh')" />
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { Refresh } from '@element-plus/icons-vue'
import IconBtn from './IconBtn.vue'

defineProps({ loading: Boolean })
defineEmits(['refresh'])
const { t } = useI18n()
</script>
