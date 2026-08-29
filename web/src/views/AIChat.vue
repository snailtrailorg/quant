<template>
  <el-card style="height: calc(100vh - 140px); display: flex; flex-direction: column">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>{{ t('chat.title') }}</span>
        <el-tag>{{ t('chat.tag') }}</el-tag>
      </div>
    </template>
    <div class="chat-body" ref="body">
      <div v-for="(msg, i) in messages" :key="i" :class="['msg', msg.role]">
        <!-- P2-12（05 §5.9）：Markdown 渲染——量化回复必含代码与表格，纯文本不可读 -->
        <div class="bubble md" v-html="renderMd(msg.content)"></div>
      </div>
      <div v-if="loading" class="msg assistant"><div class="bubble">{{ streamingText || t('chat.thinking') }}</div></div>
    </div>
    <div class="chat-input">
      <el-input
        v-model="input" :placeholder="t('chat.ph')" type="textarea" :rows="2"
        @keydown.enter.exact.prevent="onSend" :disabled="loading">
        <template #append>
          <el-button v-if="!loading" type="primary" @click="onSend" :icon="Promotion">{{ t('chat.send') }}</el-button>
          <el-button v-else type="warning" @click="onStop">{{ t('chat.stopGen') }}</el-button>
        </template>
      </el-input>
      <div style="margin-top: 8px">
        <el-button type="primary" @click="quick(t('chat.qPosition'))">{{ t('chat.qPosition') }}</el-button>
        <el-button type="primary" @click="quick(t('chat.qPnl'))">{{ t('chat.qPnl') }}</el-button>
        <el-button type="primary" @click="quick(t('chat.qStatus'))">{{ t('chat.qStatus') }}</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, nextTick, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { Promotion } from '@element-plus/icons-vue'
import { chat } from '../api'

const { t } = useI18n()
const messages = ref([
  { role: 'assistant', content: t('chat.greeting') }
])
const input = ref('')
const loading = ref(false)
const sending = ref(false)
const body = ref(null)
const streamingText = ref('')
let ws = null

const scroll = () => nextTick(() => { if (body.value) body.value.scrollTop = body.value.scrollHeight })

// P2-12：mini Markdown 渲染（零依赖—— fenced code/inline code/bold/表格式行）。只读助手,
// 内容源为 LLM 输出经后端只读工具——v-html 前做 HTML 转义防注入。
const esc = (x) => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const renderMd = (src) => {
  const parts = String(src ?? '').split(/```/)
  return parts.map((seg, i) => {
    if (i % 2 === 1) return `<pre class="md-code">${esc(seg.replace(/^\w*\n/, ''))}</pre>`
    let h = esc(seg)
      .replace(/`([^`\n]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    // 表格行（含 | 的连续行）→ <table>
    h = h.split('\n').map(line =>
      line.trim().startsWith('|') && line.trim().endsWith('|')
        ? '<tr>' + line.trim().slice(1, -1).split('|').map(c => `<td>${c.trim()}</td>`).join('') + '</tr>'
        : line)
      .join('\n')
    if (h.includes('<tr>')) h = `<table class="md-tab">${h}</table>`
    return h.replace(/\n/g, '<br>')
  }).join('')
}
// P2-12：停止生成（关闭 WS 流）
const onStop = () => { try { ws?.close() } catch {} loading.value = false }

onUnmounted(() => { if (ws) { ws.close(); ws = null } })

const onSend = async () => {
  const text = input.value.trim()
  if (!text || sending.value) return
  sending.value = true
  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true
  streamingText.value = ''
  scroll()
  try {
    // P2-10：优先 WS 流式，fallback POST
    const token = localStorage.getItem('token')
    if (token && window.WebSocket) {
      await new Promise((resolve) => {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        ws = new WebSocket(`${protocol}//${location.host}/ws/chat`)
        let connected = false
        ws.onopen = () => {
          connected = true
          ws.send(JSON.stringify({ type: 'auth', token }))
          ws.send(JSON.stringify({ messages: [{ role: 'user', content: text }] }))
        }
        ws.onmessage = (e) => {
          if (e.data === '[DONE]') { ws.close(); ws = null; resolve() }
          else { streamingText.value += e.data; scroll() }
        }
        ws.onerror = () => { if (!connected) resolve() }
        ws.onclose = () => { resolve() }
        setTimeout(() => { if (ws && ws.readyState === 1) {} else resolve() }, 25000)
      }).then(() => {
        if (streamingText.value) {
          messages.value.push({ role: 'assistant', content: streamingText.value })
          streamingText.value = ''
        } else {
          throw new Error('ws 空')
        }
      }).catch(async () => {
        // fallback POST
        const res = await chat(text)
        messages.value.push({ role: 'assistant', content: res.reply })
      })
    } else {
      const res = await chat(text)
      messages.value.push({ role: 'assistant', content: res.reply })
    }
  } catch (e) {
    console.error(e)
    messages.value.push({ role: 'assistant', content: t('chat.failed') })
  } finally {
    loading.value = false
    sending.value = false
    scroll()
  }
}
const quick = q => { input.value = q; onSend() }
</script>

<style scoped>
.chat-body { flex: 1; overflow-y: auto; padding: 12px; background: #f5f7fa; border-radius: 4px; }
.msg { margin-bottom: 12px; display: flex; }
.msg.user { justify-content: flex-end; }
.msg.assistant { justify-content: flex-start; }
.bubble { max-width: 70%; padding: 10px 14px; border-radius: 10px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.msg.user .bubble { background: #409eff; color: #fff; }
.msg.assistant .bubble { background: #fff; color: #303133; border: 1px solid #e4e7ed; }
.chat-input { margin-top: 12px; }
</style>
/* P2-12：Markdown 渲染样式（v-html 需 :deep） */
.bubble.md :deep(pre.md-code) { background: #1e2430; color: #e6eaf2; padding: 10px; border-radius: 6px; overflow-x: auto; font-family: var(--font-num); font-size: 12px; }
.bubble.md :deep(code) { background: rgba(31, 79, 216, 0.08); padding: 1px 4px; border-radius: 3px; font-family: var(--font-num); }
.bubble.md :deep(table.md-tab) { border-collapse: collapse; margin: 6px 0; }
.bubble.md :deep(table.md-tab td) { border: 1px solid var(--border-weak); padding: 3px 8px; font-size: 12px; }
