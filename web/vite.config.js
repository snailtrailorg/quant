import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import VueI18nPlugin from '@intlify/unplugin-vue-i18n/vite'

export default defineConfig({
  plugins: [
    vue(),
    // 批 132：vue-i18n 词条**构建期预编译**（运行时不再 new Function ⇒ 可撤 CSP unsafe-eval）。
    // dropMessageCompiler=true 剥掉运行时编译器；JIT flag 让 9.x 运行时认插件产出的 AST
    // （缺它会 build 绿但运行时 SyntaxError + 登录页白屏——双盲审 A/B 均实证）。
    // ⚠ package.json 是 "type": "module" ⇒ **无 __dirname**，include 必须用 import.meta.url。
    VueI18nPlugin({
      include: fileURLToPath(new URL('./src/locales/**', import.meta.url)),
      dropMessageCompiler: true,
    }),
  ],
  define: { __INTLIFY_JIT_COMPILATION__: 'true' },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
  // 本机彩排验证规约（2026-10-14 用户裁定）：彩排后视觉验证用 vite preview 起同 dist
  // 产物（=staging 同 hash bundle）。preview 固定 5174 与 dev 栈 5173 分端口并存
  // （互不挤占——同端口时后起 vite 自动跳端口，现场难排查）；转发同 dev 栈指向
  // 本机 8000（dev-start.sh 起的后端）——本地验证全链本地闭环，不依赖 prod/外网。
  preview: {
    port: 5174,
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
          // 批 132：locale 表（预编译后 ~1.31MB 裸）独立成 chunk——防 entry 从 60KB gzip 涨到 115KB
          if (id.includes('/src/locales/')) return 'locales'
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