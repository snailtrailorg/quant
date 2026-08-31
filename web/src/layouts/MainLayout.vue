<template>
  <el-container style="height: 100vh">
    <el-aside :width="collapsed ? '64px' : '240px'" style="background: var(--bg-sidebar); transition: width .2s">
      <div style="color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center">
        {{ t('app.title') }}
      </div>
      <el-menu :default-active="route.path" router background-color="var(--bg-sidebar)" text-color="#bfcbd9" active-text-color="#FFFFFF" style="padding-bottom: 28px; --el-menu-item-height: 40px; --el-menu-sub-item-height: 40px">
        <!-- P3-9（web-design 03 v2.1）：菜单 v2.1 四组 16 项——组标题与菜单项同字号;组内流程序 -->
        <el-menu-item index="/"><el-icon><DataBoard /></el-icon>{{ t('nav.dashboard') }}</el-menu-item>

        <el-sub-menu index="research">
          <template #title><el-icon><DataAnalysis /></el-icon>{{ t('nav.gResearch') }}</template>
          <el-menu-item index="/screener"><el-icon><Search /></el-icon>{{ t('nav.screener') }}</el-menu-item>
          <el-menu-item index="/pool"><el-icon><Collection /></el-icon>{{ t('nav.stockPool') }}</el-menu-item>
          <el-menu-item index="/factors"><el-icon><MagicStick /></el-icon>{{ t('nav.factors') }}</el-menu-item>
          <el-menu-item index="/strategy"><el-icon><SetUp /></el-icon>{{ t('nav.strategy') }}</el-menu-item>
          <el-menu-item index="/backtest"><el-icon><Timer /></el-icon>{{ t('nav.backtest') }}</el-menu-item>
          <el-menu-item index="/analysis"><el-icon><TrendCharts /></el-icon>{{ t('nav.dailyInsight') }}</el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="live">
          <template #title><el-icon><Monitor /></el-icon>{{ t('nav.gLive') }}</template>
          <el-menu-item index="/live-task"><el-icon><VideoPlay /></el-icon>{{ t('nav.liveTasks') }}</el-menu-item>
          <el-menu-item index="/trading"><el-icon><Coin /></el-icon>{{ t('nav.tradingDesk') }}</el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="riskgrp">
          <template #title><el-icon><Warning /></el-icon>{{ t('nav.gRisk') }}</template>
          <el-menu-item index="/risk"><el-icon><CircleCheck /></el-icon>{{ t('nav.risk') }}</el-menu-item>
          <el-menu-item index="/reconcile"><el-icon><ScaleToOriginal /></el-icon>{{ t('nav.reconcile') }}</el-menu-item>
          <el-menu-item index="/risk-rules"><el-icon><List /></el-icon>{{ t('nav.riskRules') }}</el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="ops" v-if="['admin', 'analyst'].includes(role)">
          <template #title><el-icon><Setting /></el-icon>{{ t('nav.gOps') }}</template>
          <el-menu-item index="/dataops"><el-icon><FolderOpened /></el-icon>{{ t('nav.dataCenter') }}</el-menu-item>
          <el-menu-item v-if="role === 'admin'" index="/integrations"><el-icon><Link /></el-icon>{{ t('nav.gIntegrations') }}</el-menu-item>
          <el-menu-item index="/observe"><el-icon><FirstAidKit /></el-icon>{{ t('nav.healthLogs') }}</el-menu-item>
          <el-menu-item v-if="role === 'admin'" index="/settings"><el-icon><Tools /></el-icon>{{ t('nav.settings') }}</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="background: var(--bg-surface); border-bottom: 1px solid #eee; display: flex; align-items: center; justify-content: space-between">
        <div></div>
        <div style="display: flex; align-items: center; gap: 16px">
          <!-- P3-2（04 §4.1）：侧边栏折叠（240↔64 图标模式，记忆状态） -->
          <el-button size="small" text @click="collapsed = !collapsed">{{ collapsed ? '»' : '«' }}</el-button>

          <!-- P1-4（05 §5.2 要点 9）：⛔ 急停常驻顶栏（火警时不该先找消防栓在几楼） -->
          <el-button type="danger" size="small" @click="onEmergencyHalt">{{ t('risk.halt') }}</el-button>

          <!-- P1-4：数据健康灯（admin-only 端点,非 admin 隐藏——B-P2-8 修正恒黄误报） -->
          <el-popover v-if="role === 'admin'" placement="bottom-end" :width="320" trigger="click">
            <template #reference>
              <span :style="{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }">
                <span class="dot" :class="healthLevel" style="width:10px;height:10px;border-radius:50%;display:inline-block" />
                <span style="font-size: 12px">{{ t('layout.healthLight') }}</span>
              </span>
            </template>
            <b>{{ t('layout.healthSummary') }}</b>
            <div v-for="h in healthItems" :key="h.k" style="display:flex; justify-content:space-between; padding:4px 0; font-size:13px">
              <span>{{ h.k }}</span><span :class="h.ok ? 'up' : 'down'">{{ h.v }}</span>
            </div>
            <div style="color: #909399; font-size: 12px; margin-top: 6px">{{ t('layout.healthNote') }}</div>
          </el-popover>

          <el-select v-model="lang" @change="onLangChange" style="width: 110px">
            <el-option v-for="l in LANGUAGES" :key="l.code" :label="l.label" :value="l.code" />
          </el-select>

          <!-- 通知铃铛（按角色可见类别；viewer 无可见类别不显示） -->
          <!-- P1-6（05 §5.0-2）：通知中心 480 抽屉替代 popover（结构化 body+精确路由） -->
          <el-badge v-if="bellVisible" :value="notifCount" :hidden="!notifCount" :max="99">
            <el-button type="primary" circle @click="notifDrawer = true">🔔</el-button>
          </el-badge>
          <el-drawer v-model="notifDrawer" :title="t('notify.title')" size="480px">
            <div style="display: flex; justify-content: flex-end; margin-bottom: 8px">
              <el-button v-if="notifCount" size="small" type="primary" @click="onAckAll">{{ t('notify.ackAll') }}</el-button>
            </div>
            <div style="overflow-y: auto">
              <div v-if="!notifs.length" style="color: #909399; font-size: 13px; text-align: center; padding: 20px 0">{{ t('notify.empty') }}</div>
              <div v-for="n in notifs" :key="n.id" @click="goCategory(n.category)"
                style="padding: 10px 4px; border-bottom: 1px solid #f0f0f0; cursor: pointer">
                <span :class="['dot', n.level]"></span>
                <b style="font-size: 13px">{{ n.title }}</b>
                <div v-if="n.body" class="notif-body">{{ n.body }}</div>
                <div style="color: #909399; font-size: 12px; margin-left: 14px">{{ n.created_at }}</div>
              </div>
            </div>
          </el-drawer>

          <!-- 03 v2.1:AI 助手顶栏常驻入口(不占菜单位,全局只读工具) -->
          <el-button circle @click="$router.push('/chat')"><el-icon><ChatDotRound /></el-icon></el-button>

          <!-- P3-8（09-B8）：帮助抽屉（全角色）+ P3-2 暗色切换（盯盘场景） -->
          <el-button circle @click="helpDrawer = true"><el-icon><QuestionFilled /></el-icon></el-button>
          <el-drawer v-model="helpDrawer" :title="t('layout.helpTitle')" size="480px">
            <Help />
          </el-drawer>
          <el-switch v-model="dark" :active-icon="Moon" :inactive-icon="Sunny" @change="onDark" />

          <!-- 用户区：头像 + 昵称下拉（个人中心/退出 + 我的权限玻璃盒，10 §4） -->
          <el-dropdown trigger="click" @command="onUserCommand">
            <div style="display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 4px 8px; border-radius: 6px;">
              <Avatar :url="avatarUrl" :name="nickname || username" size="sm" />
              <span style="font-size: 14px">{{ nickname || username }}</span>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">{{ t('profile.title') }}</el-dropdown-item>
                <el-dropdown-item command="myperms">{{ t('layout.myPerms') }}</el-dropdown-item>
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
    <!-- 我的权限玻璃盒（10 §4：被授予/拒绝的依据用户随时可见） -->
  <el-dialog v-model="showMyPerms" :title="t('layout.myPerms')" width="420px">
    <div style="margin-bottom: 8px; color: var(--text-secondary)">{{ t('layout.myPermsNote') }}</div>
    <el-tag v-for="p in myPerms" :key="p" style="margin: 4px">{{ p }}</el-tag>
    <div v-if="!myPerms.length" style="color: var(--text-secondary)">—</div>
  </el-dialog>
  <!-- ⌘K 全局搜索(P1-4/03 §3.3) -->
  <el-dialog v-model="cmdkVisible" :title="t('layout.search')" width="480px" :show-close="false">
    <el-input v-model="cmdkQuery" :placeholder="t('layout.searchPh')" autofocus @input="filterCmdk" />
    <div style="max-height: 300px; overflow-y: auto; margin-top: 8px">
      <div v-for="item in cmdkResults" :key="item.path" @click="$router.push(item.path); cmdkVisible = false"
        style="padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--border-weak); display: flex; justify-content: space-between">
        <span>{{ item.label }}</span>
        <span style="color: var(--text-secondary); font-size: 12px">{{ item.path }}</span>
      </div>
      <div v-if="!cmdkResults.length" style="color: var(--text-secondary); text-align: center; padding: 20px">{{ t('layout.noResults') }}</div>
    </div>
  </el-dialog>
</el-container>
</template>

<script setup>
import { QuestionFilled, DataBoard, DataAnalysis, Search, MagicStick, SetUp, Timer,
         TrendCharts, Collection, Monitor, Coin, VideoPlay, Odometer, Warning, CircleCheck,
         ScaleToOriginal, List, Setting, FolderOpened, Link, FirstAidKit, Lock,
         ChatDotRound } from '@element-plus/icons-vue'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
  else if (cmd === 'myperms') { loadMyPerms(); showMyPerms.value = true }
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
// P3-2/P3-8：折叠+暗色+帮助抽屉+我的权限玻璃盒
// 14号 P0:侧栏 matchMedia 自动折叠(localStorage 记忆优先)
const collapsed = ref(localStorage.getItem('sidebar-collapsed') === '1')
const _mq = window.matchMedia('(max-width: 1706px)')
const _onMq = e => { if (!localStorage.getItem('sidebar-collapsed')) collapsed.value = e.matches }
_mq.addEventListener('change', _onMq)
if (!localStorage.getItem('sidebar-collapsed')) collapsed.value = _mq.matches
const dark = ref(localStorage.getItem('theme-dark') === '1')
const onDark = v => { document.documentElement.classList.toggle('dark', v); localStorage.setItem('theme-dark', v ? '1' : '0') }
if (dark.value) document.documentElement.classList.add('dark')
const helpDrawer = ref(false)
const myPerms = ref([])
const showMyPerms = ref(false)
const loadMyPerms = async () => {
  try { const { getMe } = await import('../api'); myPerms.value = (await getMe()).permissions || [] } catch {}
}
import { Moon, Sunny } from '@element-plus/icons-vue'
import Help from '../views/Help.vue'

// P1-4：急停（熔断=轻确认,04 §4.5——所有可登录角色可触发,后端 require_perm 兜底）
const onEmergencyHalt = async () => {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt'), t('common.confirm'), { type: 'warning' })
    const { riskHalt } = await import('../api')
    await riskHalt(); ElMessage.success(t('risk.halted'))
  } catch (e) { if (e?.response) ElMessage.error(String(e)) }
}
// P1-4：数据健康灯摘要（抽屉自含诊断;权限感知——不跨页路由）
const notifDrawer = ref(false)
const healthLevel = ref('ok')
const healthItems = ref([])
const loadHealth = async () => {
  try {
    const { getHealthComponents } = await import('../api')
    const comps = await getHealthComponents()
    const items = (comps.items || comps || []).map(c => ({ k: c.name || c.component || 'svc', ok: (c.status || 'ok') === 'ok', v: c.status || 'ok' }))
    healthItems.value = items.slice(0, 8)
    healthLevel.value = items.some(i => !i.ok) ? 'critical' : 'ok'
  } catch { healthItems.value = [{ k: 'health', ok: false, v: '—' }]; healthLevel.value = 'warn' }
}
// runbook: 15号批零②——死代码已删;通知 runbook 接线待通知表加 code 字段后做(待办已立)
const goCategory = c => router.push({ email: '/settings?tab=run', task: '/dataops?tab=sched', risk: '/risk', data: '/dataops?tab=integrity', system: '/observe?tab=health' }[c] || '/')
loadHealth()
onMounted(() => { loadNotifs(); notifTimer = setInterval(loadNotifs, 60000) })
onUnmounted(() => { if (notifTimer) clearInterval(notifTimer) })

const onLangChange = v => setLang(v)
const logout = async () => {
  try { await api.post('/auth/logout') } catch {}
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  router.push('/login')
}

// ⌘K 全局搜索
const cmdkVisible = ref(false)
const cmdkQuery = ref('')
const cmdkResults = ref([])
const searchIndex = [
  { label: '选股器', path: '/screener' }, { label: '股票池', path: '/pool' },
  { label: '因子库', path: '/factors' }, { label: '策略', path: '/strategy' },
  { label: '回测', path: '/backtest' }, { label: '每日研判', path: '/analysis' },
  { label: '实盘任务', path: '/live-task' }, { label: '交易台', path: '/trading' },
  { label: '风控总览', path: '/risk' }, { label: '三账对账', path: '/reconcile' },
  { label: '数据中心', path: '/dataops' }, { label: '集成中心', path: '/integrations' },
  { label: '健康与日志', path: '/observe' }, { label: '设置', path: '/settings' },
  { label: 'AI 助手', path: '/chat' },
]
const filterCmdk = () => {
  const q = cmdkQuery.value.toLowerCase()
  cmdkResults.value = q ? searchIndex.filter(i => i.label.toLowerCase().includes(q) || i.path.includes(q)) : searchIndex
}
const onKeydown = (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); cmdkVisible.value = !cmdkVisible.value; filterCmdk() }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
/* 通知级别色点（critical 红 / warn 橙 / info 灰），与 el-tag 语义色一致 */
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot.critical { background: #f56c6c; }
.dot.warn { background: #e6a23c; }
.dot.info { background: #909399; }
.notif-body { white-space: pre-wrap; color: #606266; font-size: 12px; line-height: 1.5; margin: 4px 0 2px 14px; max-height: 4.5em; overflow: hidden; }
</style>
