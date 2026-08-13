<template>
  <el-card style="height: calc(100vh - 140px); display: flex; flex-direction: column">
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>🤖 AI 助手（自然语言查询）</span>
        <el-tag size="small">只读工具 · 下单类不开放</el-tag>
      </div>
    </template>
    <div class="chat-body" ref="body">
      <div v-for="(msg, i) in messages" :key="i" :class="['msg', msg.role]">
        <div class="bubble" v-text="msg.content"></div>
      </div>
      <div v-if="loading" class="msg assistant"><div class="bubble">{{ streamingText || '思考中...' }}</div></div>
    </div>
    <div class="chat-input">
      <el-input
        v-model="input" placeholder="输入查询，如：现在持仓多少？今天盈亏？BTC策略什么状态？"
        @keyup.enter="onSend" :disabled="loading" clearable>
        <template #append>
          <el-button @click="onSend" :loading="loading" :icon="Promotion">发送</el-button>
        </template>
      </el-input>
      <div style="margin-top: 8px">
        <el-button size="small" @click="quick('查持仓')">查持仓</el-button>
        <el-button size="small" @click="quick('今天盈亏')">今天盈亏</el-button>
        <el-button size="small" @click="quick('策略运行状态')">策略状态</el-button>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ref, nextTick, onUnmounted } from 'vue'
import { Promotion } from '@element-plus/icons-vue'
import { chat } from '../api'

const messages = ref([
  { role: 'assistant', content: '你好，我是 AI 助手。可以帮你查持仓、盈亏、策略状态、A股研判等。输入查询即可。' }
])
const input = ref('')
const loading = ref(false)
const sending = ref(false)
const body = ref(null)
const streamingText = ref('')
let ws = null

const scroll = () => nextTick(() => { if (body.value) body.value.scrollTop = body.value.scrollHeight })

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
          messages.value.push({ role: 'assistant', content: streamingText.value.replace(/\n/g, '<br>') })
          streamingText.value = ''
        } else {
          throw new Error('ws 空')
        }
      }).catch(async () => {
        // fallback POST
        const res = await chat(text)
        messages.value.push({ role: 'assistant', content: res.reply.replace(/\n/g, '<br>') })
      })
    } else {
      const res = await chat(text)
      messages.value.push({ role: 'assistant', content: res.reply.replace(/\n/g, '<br>') })
    }
  } catch (e) {
    console.error(e)
    messages.value.push({ role: 'assistant', content: '查询失败，请稍后重试' })
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