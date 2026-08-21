<template>
  <el-container style="height: 100vh">
    <el-aside width="220px" style="background: #304156">
      <div style="color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center">
        {{ t('app.title') }}
      </div>
      <el-menu :default-active="route.path" router background-color="#304156" text-color="#bfcbd9" active-text-color="#409EFF" style="padding-bottom: 28px; --el-menu-item-height: 40px; --el-menu-sub-item-height: 40px">
        <!-- 首页 -->
        <el-menu-item index="/"><el-icon><DataLine /></el-icon>{{ t('nav.dashboard') }}</el-menu-item>

        <!-- 交易工作台 -->
        <el-sub-menu index="trade">
          <template #title><el-icon><Money /></el-icon>{{ t('nav.trade') }}</template>
          <el-menu-item index="/trading"><el-icon><Wallet /></el-icon>{{ t('nav.trading') }}</el-menu-item>
          <el-menu-item index="/monitoring"><el-icon><Monitor /></el-icon>{{ t('nav.monitoring') }}</el-menu-item>
          <el-menu-item index="/tasks"><el-icon><List /></el-icon>{{ t('nav.tasks') }}</el-menu-item>
        </el-sub-menu>

        <!-- 策略实验室 -->
        <el-sub-menu index="strategy">
          <template #title><el-icon><Setting /></el-icon>{{ t('nav.strategyLab') }}</template>
          <el-menu-item index="/strategy"><el-icon><List /></el-icon>{{ t('nav.strategy') }}</el-menu-item>
          <el-menu-item index="/live-task"><el-icon><VideoPlay /></el-icon>{{ t('nav.liveTask') }}</el-menu-item>
          <el-menu-item index="/backtest"><el-icon><Histogram /></el-icon>{{ t('nav.backtest') }}</el-menu-item>
          <el-menu-item index="/pool"><el-icon><FolderOpened /></el-icon>{{ t('nav.pool') }}</el-menu-item>
          <el-menu-item index="/factors"><el-icon><MagicStick /></el-icon>{{ t('nav.factors') }}</el-menu-item>
        </el-sub-menu>

        <!-- 数据分析 -->
        <el-sub-menu index="analysis">
          <template #title><el-icon><TrendCharts /></el-icon>{{ t('nav.dataAnalysis') }}</template>
          <el-menu-item index="/ascreen"><el-icon><Search /></el-icon>{{ t('nav.ascreen') }}</el-menu-item>
          <el-menu-item index="/cbscreen"><el-icon><Coin /></el-icon>{{ t('nav.cbscreen') }}</el-menu-item>
          <el-menu-item index="/etfscreen"><el-icon><Histogram /></el-icon>{{ t('nav.etfscreen') }}</el-menu-item>
          <el-menu-item index="/analysis"><el-icon><TrendCharts /></el-icon>{{ t('nav.analysis') }}</el-menu-item>
          <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon>{{ t('nav.chat') }}</el-menu-item>
        </el-sub-menu>

        <!-- 风控 -->
        <el-sub-menu index="risk">
          <template #title><el-icon><Warning /></el-icon>{{ t('nav.riskSection') }}</template>
          <el-menu-item index="/risk"><el-icon><Shield /></el-icon>{{ t('nav.risk') }}</el-menu-item>
          <el-menu-item index="/reconcile"><el-icon><ScaleToOriginal /></el-icon>{{ t('nav.reconcile') }}</el-menu-item>
          <el-menu-item index="/risk-rules"><el-icon><Setting /></el-icon>{{ t('nav.riskRules') }}</el-menu-item>
        </el-sub-menu>

        <!-- 系统运维（Admin + Analyst 可见，Trader/Viewer 不可见） -->
        <el-sub-menu index="system" v-if="['admin', 'analyst'].includes(role)">
          <template #title><el-icon><Tools /></el-icon>{{ t('nav.ops') }}</template>
          <el-menu-item index="/data-manage"><el-icon><Download /></el-icon>{{ t('nav.dataManage') }}</el-menu-item>
          <el-menu-item index="/data-integrity"><el-icon><DataAnalysis /></el-icon>{{ t('nav.dataIntegrity') }}</el-menu-item>
          <el-menu-item index="/health"><el-icon><FirstAidKit /></el-icon>{{ t('nav.health') }}</el-menu-item>
          <el-menu-item index="/help"><el-icon><QuestionFilled /></el-icon>{{ t('nav.help') }}</el-menu-item>
          <el-menu-item index="/logs"><el-icon><Document /></el-icon>{{ t('nav.logs') }}</el-menu-item>
        </el-sub-menu>

        <!-- 账户设置（仅 Admin） -->
        <el-sub-menu index="account-group" v-if="role === 'admin'">
          <template #title><el-icon><User /></el-icon>{{ t('nav.settings') }}</template>
          <el-menu-item index="/account"><el-icon><Key /></el-icon>{{ t('nav.account') }}</el-menu-item>
          <el-menu-item index="/audit"><el-icon><Tickets /></el-icon>{{ t('nav.audit') }}</el-menu-item>
          <el-menu-item index="/llm-models"><el-icon><ChatDotRound /></el-icon>{{ t('nav.llmModels') }}</el-menu-item>
          <el-menu-item index="/im-bots"><el-icon><ChatDotRound /></el-icon>{{ t('nav.feishu') }}</el-menu-item>
          <el-menu-item index="/system-config"><el-icon><Tools /></el-icon>{{ t('nav.systemConfig') }}</el-menu-item>
          <el-menu-item index="/data-sources"><el-icon><Connection /></el-icon>{{ t('nav.dataSources') }}</el-menu-item>
          <el-menu-item index="/channels"><el-icon><ChatDotRound /></el-icon>{{ t('nav.channels') }}</el-menu-item>
          <el-menu-item index="/brokers"><el-icon><Wallet /></el-icon>{{ t('nav.brokers') }}</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="background: #fff; border-bottom: 1px solid #eee; display: flex; align-items: center; justify-content: space-between">
        <div></div>
        <div style="display: flex; align-items: center; gap: 16px">
          <el-select v-model="lang" @change="onLangChange" style="width: 110px">
            <el-option v-for="l in LANGUAGES" :key="l.code" :label="l.label" :value="l.code" />
          </el-select>

          <!-- 通知铃铛（按角色可见类别；viewer 无可见类别不显示） -->
          <el-popover v-if="bellVisible" placement="bottom-end" :width="380" trigger="click">
            <template #reference>
              <el-badge :value="notifCount" :hidden="!notifCount" :max="99">
                <el-button type="primary" circle>🔔</el-button>
              </el-badge>
            </template>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
              <b>{{ t('notify.title') }}</b>
              <el-button v-if="notifCount" size="small" type="primary" @click="onAckAll">{{ t('notify.ackAll') }}</el-button>
            </div>
            <div style="max-height: 320px; overflow-y: auto">
              <div v-if="!notifs.length" style="color: #909399; font-size: 13px; text-align: center; padding: 20px 0">{{ t('notify.empty') }}</div>
              <div v-for="n in notifs" :key="n.id" @click="goCategory(n.category)"
                style="padding: 8px 4px; border-bottom: 1px solid #f0f0f0; cursor: pointer">
                <span :class="['dot', n.level]"></span>
                <b style="font-size: 13px">{{ n.title }}</b>
                <div v-if="n.body" class="notif-body">{{ n.body }}</div>
                <div style="color: #909399; font-size: 12px; margin-left: 14px">{{ n.created_at }}</div>
              </div>
            </div>
          </el-popover>

          <!-- 用户区：头像 + 昵称下拉（个人中心/退出），替换原文字 tag（批次C） -->
          <el-dropdown trigger="click" @command="onUserCommand">
            <div style="display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 8px; border-radius: 6px;">
              <Avatar :url="avatarUrl" :name="nickname || username" size="sm" />
              <span style="font-size: 14px">{{ nickname || username }}</span>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">{{ t('profile.title') }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided>{{ t('user.logout') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main style="padding-bottom: 30px">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { QuestionFilled } from '@element-plus/icons-vue'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getMe, getNotifications, ackAllNotifications } from '../api'
import api from '../api'
import { setLang, LANGUAGES } from '../i18n'
import Avatar from '../components/Avatar.vue'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const username = ref('')
const nickname = ref('')
const avatarUrl = ref('')
const role = ref('')
const lang = ref(locale.value)

getMe().then(me => { username.value = me.username; role.value = me.role; nickname.value = me.nickname || ''; avatarUrl.value = me.avatar_url || '' }).catch(e => { console.error(e); username.value = ''; role.value = '' })
// 用户下拉命令（个人中心/退出）
const onUserCommand = (cmd) => {
  if (cmd === 'profile') router.push('/profile')
  else if (cmd === 'logout') logout()
}

// ——— 通知铃铛（60s 轮询；viewer 无可见类别不显示）———
const notifs = ref([])
const notifCount = ref(0)
let notifTimer = null
const bellVisible = computed(() => ['admin', 'trader', 'analyst'].includes(role.value))
const loadNotifs = async () => {
  if (!bellVisible.value) return
  try {
    const r = await getNotifications('active', 20)
    notifs.value = r.items || []
    notifCount.value = r.count || 0
  } catch {}
}
const onAckAll = async () => {
  try { await ackAllNotifications(); await loadNotifs() } catch {}
}
// 类别 → 页面路由（点击通知直达）
const goCategory = c => router.push({ email: '/logs', task: '/tasks', risk: '/risk', data: '/data-integrity', system: '/logs' }[c] || '/')
onMounted(() => { loadNotifs(); notifTimer = setInterval(loadNotifs, 60000) })
onUnmounted(() => { if (notifTimer) clearInterval(notifTimer) })

const onLangChange = v => setLang(v)
const logout = async () => {
  try { await api.post('/auth/logout') } catch {}
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  router.push('/login')
}
</script>

<style scoped>
/* 通知级别色点（critical 红 / warn 橙 / info 灰），与 el-tag 语义色一致 */
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot.critical { background: #f56c6c; }
.dot.warn { background: #e6a23c; }
.dot.info { background: #909399; }
.notif-body { white-space: pre-wrap; color: #606266; font-size: 12px; line-height: 1.5; margin: 4px 0 2px 14px; max-height: 4.5em; overflow: hidden; }
</style>
