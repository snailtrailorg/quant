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
        <div class="bubble" v-text="msg.content"></div>
      </div>
      <div v-if="loading" class="msg assistant"><div class="bubble">{{ streamingText || t('chat.thinking') }}</div></div>
    </div>
    <div class="chat-input">
      <el-input
        v-model="input" :placeholder="t('chat.ph')"
        @keyup.enter="onSend" :disabled="loading" clearable>
        <template #append>
          <el-button type="primary" @click="onSend" :loading="loading" :icon="Promotion">{{ t('chat.send') }}</el-button>
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