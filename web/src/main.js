import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'   // 暗色 EP 变量（html.dark 激活;P3-1）
import './styles/tokens.css'                          // 设计令牌（P3-1,04 全文）
import 'vue-cropper/dist/index.css'  // 全局引入（注意：该包 CSS 自带 [data-v-a742df44] 且组件 JS 硬编码同款 __scopeId，两者自洽——只要引入即生效）
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import i18n from './i18n'

const app = createApp(App)

for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, comp)
}

app.use(ElementPlus)
app.use(router)
app.use(i18n)
app.mount('#app')

// W6 补（盲审 B-P2）：Monaco editor worker——缺配置时 word-based 补全降级+console 报错
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker'
self.MonacoEnvironment = { getWorker: () => editorWorker }
