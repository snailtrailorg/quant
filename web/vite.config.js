import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vue 核心框架
          if (id.includes('node_modules/vue/') || id.includes('node_modules/@vue/') ||
              id.includes('node_modules/vue-router') || id.includes('node_modules/vue-i18n')) {
            return 'vue-vendor'
          }
          // Element Plus（含 @vueuse/core 依赖，避免循环）
          if (id.includes('node_modules/element-plus') || id.includes('node_modules/@element-plus') ||
              id.includes('node_modules/@vueuse') || id.includes('node_modules/@popperjs') ||
              id.includes('node_modules/@floating-ui') || id.includes('node_modules/async-validator')) {
            return 'element-plus'
          }
          // echarts（图表库，多页共享）
          if (id.includes('node_modules/echarts') || id.includes('node_modules/vue-echarts') ||
              id.includes('node_modules/zrender')) {
            return 'echarts'
          }
          // klinecharts（仅 KlineDialog 用，独立 chunk）
          if (id.includes('node_modules/klinecharts')) {
            return 'klinecharts'
          }
          // 其他工具
          if (id.includes('node_modules/axios') || id.includes('node_modules/dayjs')) {
            return 'utils'
          }
        },
      },
    },
  },
})