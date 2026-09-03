<template>
  <!-- 通用头像：自定义/系统图标（URL）→ 缺省=按名字确定性随机卡通图标（36 个）→ 加载失败 CSS 首字母兜底 -->
  <div class="avatar" :style="{ width: sizePx, height: sizePx, fontSize }">
    <img v-if="displayUrl && !imgFailed" :src="displayUrl" :alt="name" class="avatar-img" @error="imgFailed = true" />
    <span v-else class="avatar-fallback" :style="{ background: color }">{{ initial }}</span>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  url: { type: String, default: '' },        // 头像 URL（空=按名字随机系统图标）
  name: { type: String, default: '?' },      // 用户名/昵称（随机种子 + 首字母兜底）
  size: { type: String, default: 'md' },     // sm=24 md=40 lg=64
})

const imgFailed = ref(false)
watch(() => props.url, () => { imgFailed.value = false })

function hashName(str) {
  let h = 0
  for (const c of (str || '?')) h = ((h << 5) - h + c.charCodeAt(0)) | 0
  return Math.abs(h)
}
const sizes = { sm: 24, md: 40, lg: 64 }
const sizePx = computed(() => (sizes[props.size] || 40) + 'px')
const fontSize = computed(() => Math.round((sizes[props.size] || 40) * 0.42) + 'px')
// 缺省：36 个卡通图标按名字确定性选择（同一用户每次相同）
const displayUrl = computed(() => props.url || `/icons/icon_${String(hashName(props.name) % 36).padStart(2, '0')}.png`)
const initial = computed(() => (props.name || '?').charAt(0).toUpperCase())
// 头像底色调色板：按名字 hash 区分"人色"（8 hue），语义=身份区分非 UI 状态色——令牌豁免（wd-20 §2.3-C），勿令牌化
const COLORS = ['var(--brand-600)', '#67c23a', '#e6a23c', '#f56c6c', 'var(--text-secondary)', '#7a5fd0', '#2fa8c5', '#c25cc2']
const color = computed(() => COLORS[hashName(props.name) % COLORS.length])
</script>

<style scoped>
.avatar { position: relative; border-radius: 50%; overflow: hidden; flex-shrink: 0; display: inline-flex; }
.avatar-img { width: 100%; height: 100%; object-fit: cover; }
.avatar-fallback { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: bold; }
</style>
