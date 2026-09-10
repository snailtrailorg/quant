<template>
  <el-container style="height: 100vh">
    <!-- 主界面框架改版（2026-09-09 用户裁定）：顶栏全宽顶到最左 + 标题居左；侧栏移出文档流改 hover 覆盖式 -->
    <el-header :class="{ 'pinned-shift': railPinned }" style="background: var(--bg-surface); border-bottom: 1px solid var(--border-weak); display: flex; align-items: center; justify-content: space-between" @mouseenter="railOpen = false">
      <div style="display: flex; align-items: center; gap: 12px">
        <!-- 钉死态隐藏（2026-09-10 四轮裁定）：侧栏 title 到顶顶替,避免双标题 -->
        <span v-show="!railPinned" class="app-title">{{ t('app.title') }}</span>
      </div>
      <div style="display: flex; align-items: center; gap: 16px">
          <!-- P1-4（05 §5.2 要点 9）：⛔ 急停常驻顶栏（火警时不该先找消防栓在几楼）。2026-09-10 用户裁定：图标按钮（原文字按钮）+title 提示 -->
          <el-button type="danger" size="small" circle :title="t('risk.halt')" @click="onEmergencyHalt"><el-icon><SwitchButton /></el-icon></el-button>

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
            <div style="color: var(--text-secondary); font-size: 12px; margin-top: 6px">{{ t('layout.healthNote') }}</div>
          </el-popover>

          <el-select v-model="lang" @change="onLangChange" style="width: 110px">
            <el-option v-for="l in LANGUAGES" :key="l.code" :label="l.label" :value="l.code" />
          </el-select>

          <!-- 通知铃铛（按角色可见类别；viewer 无可见类别不显示） -->
          <!-- P1-6（05 §5.0-2）：通知中心 480 抽屉替代 popover（结构化 body+精确路由） -->
          <el-badge v-if="bellVisible" :value="notifCount" :hidden="!notifCount" :max="99">
            <el-button type="primary" circle :title="t('notify.title')" @click="notifDrawer = true">🔔</el-button>
          </el-badge>
          <el-drawer v-model="notifDrawer" :title="t('notify.title')" size="480px">
            <div style="display: flex; justify-content: flex-end; margin-bottom: var(--sp-2)">
              <el-button v-if="notifCount" size="small" type="primary" @click="onAckAll">{{ t('notify.ackAll') }}</el-button>
            </div>
            <div style="overflow-y: auto">
              <div v-if="!notifs.length" style="color: var(--text-secondary); font-size: 13px; text-align: center; padding: 20px 0">{{ t('notify.empty') }}</div>
              <div v-for="n in notifs" :key="n.id" @click="goCategory(n.category)"
                style="padding: 10px 4px; border-bottom: 1px solid var(--border-weak); cursor: pointer">
                <span :class="['dot', n.level]"></span>
                <b style="font-size: 13px">{{ n.title }}</b>
                <el-tag v-if="n.rb" size="small" effect="plain"
                        style="margin-left: 6px">{{ n.rb.label }}</el-tag>
                <div v-if="n.body" class="notif-body">{{ n.body }}</div>
                <div v-if="n.rb" class="notif-guide">▸ {{ n.rb.guide }}</div>
                <div style="color: var(--text-secondary); font-size: 12px; margin-left: 14px">{{ n.created_at }}</div>
              </div>
            </div>
          </el-drawer>

          <!-- 03 v2.1:AI 助手顶栏常驻入口(不占菜单位,全局只读工具)；2026-09-10 图标按钮统一 title 提示 -->
          <el-button circle :title="t('nav.aiChat')" @click="$router.push('/chat')"><el-icon><ChatDotRound /></el-icon></el-button>

          <!-- P3-8（09-B8）：帮助抽屉（全角色）+ P3-2 暗色切换（盯盘场景） -->
          <el-button circle :title="t('layout.helpTitle')" @click="helpDrawer = true"><el-icon><QuestionFilled /></el-icon></el-button>
          <el-drawer v-model="helpDrawer" :title="t('layout.helpTitle')" size="480px">
            <Help />
          </el-drawer>
          <el-switch v-model="dark" :active-icon="Moon" :inactive-icon="Sunny" :title="t('layout.themeToggle')" @change="onDark" />

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
      <!-- 内层容器（图钉批补回）：root 因 el-header 判为纵向——钉死态 aside 需与 main 横排,
           显式 horizontal 包住工作区（2026-09-10 lc47 实证 column 下 order 无效） -->
      <el-container direction="horizontal" :class="{ 'pinned-shift': railPinned }" style="flex: 1 1 0%; min-height: 0">
      <!-- mouseenter 兜底关：Chrome 边界事件状态机下,指针停热区、侧栏滑到指针下方后直接跳主区,
           aside 自身从未 mouseenter→mouseleave 永不触发=卡死不收（快甩鼠标真实场景）。主区/顶栏进入=焦点离开侧栏→收 -->
      <el-main style="padding-bottom: 30px" @mouseenter="railOpen = false">
        <router-view />
      </el-main>
      <!-- 主界面框架改版：hover 热区 + 覆盖式侧栏（fixed 出文档流；z 分层 1800/1801——侧栏盖住热区,防开合抖动） -->
      <!-- 图钉（2026-09-10 用户裁定）：缺省钉死=推挤式常驻显示；拔钉=hover 覆盖式。hotzone 仅浮动态有意义 -->
      <div v-if="!railPinned" class="rail-hotzone" @mouseenter="railOpen = true"></div>
      <el-aside class="overlay-aside" :class="{ open: railOpen || railPinned, pinned: railPinned }"
                width="200px" @mouseleave="!railPinned && (railOpen = false)">
        <!-- 2026-09-10 四轮裁定：title 恢复+到顶（钉死推挤顶栏时顶替其 title）；图钉 title 右侧右对齐；
             配色恢复深底浅字（同色一体方案撤回） -->
        <el-header class="aside-brand">
          <span class="aside-brand-text">{{ t('app.title') }}</span>
          <button class="rail-pin" :class="{ active: railPinned }" :title="railPinned ? t('layout.unpin') : t('layout.pin')"
                  @click="togglePin" @mouseenter.stop @mouseleave.stop>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
                 stroke-linecap="round" stroke-linejoin="round" :style="{ transform: railPinned ? 'none' : 'rotate(45deg)' }">
              <line x1="12" y1="17" x2="12" y2="22" />
              <path d="M5 17h14l-1.5-6.5a2 2 0 0 0-2-1.5h-7a2 2 0 0 0-2 1.5Z" fill="currentColor" stroke="none" />
              <circle cx="12" cy="3.5" r="2" />
            </svg>
          </button>
        </el-header>
        <el-menu :default-active="route.path" router background-color="var(--bg-sidebar)" text-color="#bfcbd9" active-text-color="#FFFFFF" style="padding-bottom: 12px; --el-menu-item-height: 40px; --el-menu-sub-item-height: 40px">
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

          <el-sub-menu index="ops" v-if="has('data_sync') || has('system_config')">
            <template #title><el-icon><Setting /></el-icon>{{ t('nav.gOps') }}</template>
            <el-menu-item v-if="has('user_mgmt')" index="/users"><el-icon><User /></el-icon>{{ t('nav.userMgmt') }}</el-menu-item>
            <el-menu-item index="/dataops"><el-icon><FolderOpened /></el-icon>{{ t('nav.dataCenter') }}</el-menu-item>
            <el-menu-item v-if="has('llm_config') || has('im_bots_config')" index="/integrations"><el-icon><Link /></el-icon>{{ t('nav.gIntegrations') }}</el-menu-item>
            <el-menu-item index="/observe"><el-icon><FirstAidKit /></el-icon>{{ t('nav.healthLogs') }}</el-menu-item>
            <el-menu-item v-if="has('system_config')" index="/settings"><el-icon><Tools /></el-icon>{{ t('nav.settings') }}</el-menu-item>
          </el-sub-menu>
        </el-menu>

      </el-aside>
      </el-container>
    </el-container>
    <!-- 我的权限玻璃盒（10 §4：被授予/拒绝的依据用户随时可见） -->
  <el-dialog v-model="showMyPerms" :title="t('layout.myPerms')" width="480px">
    <div style="margin-bottom: var(--sp-2); color: var(--text-secondary)">{{ t('layout.myPermsNote') }}</div>
    <!-- W4 玻璃盒:分组+来源(role-base/user-override)+被拒项(10 §4 去黑箱化) -->
    <div v-if="myPermGroups.base.length" style="margin-bottom: 10px">
      <div style="font-weight: 600; font-size: 13px; margin-bottom: 4px">{{ t('perm.modeRole') }}</div>
      <el-tag v-for="p in myPermGroups.base" :key="p" style="margin: 3px">{{ p }}</el-tag>
    </div>
    <div v-if="myPermGroups.override.length" style="margin-bottom: 10px">
      <div style="font-weight: 600; font-size: 13px; margin-bottom: 4px">{{ t('perm.modeUser') }}</div>
      <el-tag v-for="p in myPermGroups.override" :key="p" type="warning" style="margin: 3px">{{ p }}</el-tag>
    </div>
    <div v-if="myPermGroups.denied.length">
      <div style="font-weight: 600; font-size: 13px; margin-bottom: 4px">{{ t('layout.myPermsDenied') }}</div>
      <el-tag v-for="p in myPermGroups.denied" :key="p" type="danger" effect="plain" style="margin: 3px">{{ p }}</el-tag>
    </div>
    <div v-if="!myPerms.length" style="color: var(--text-secondary)">—</div>
  </el-dialog>
  <!-- ⌘K 全局搜索(P1-4/03 §3.3；wd-20 §2.6 WAI-ARIA combobox：roving ↑↓/Enter/Esc) -->
  <el-dialog v-model="cmdkVisible" :title="t('layout.search')" width="480px" :show-close="false" @opened="focusCmdk">
    <div role="combobox" aria-expanded="true" aria-haspopup="listbox" aria-label="global search">
      <el-input ref="cmdkInputRef" v-model="cmdkQuery" :placeholder="t('layout.searchPh')"
                role="searchbox" aria-controls="cmdk-listbox" aria-activedescendant="cmdk-opt-active"
                @input="filterCmdk" @keydown="onCmdkKeys" />
      <div id="cmdk-listbox" role="listbox" aria-label="results" style="max-height: 300px; overflow-y: auto; margin-top: var(--sp-2)">
        <div v-for="(item, i) in cmdkResults" :key="item.path" role="option" :aria-selected="i === cmdkActive"
             :id="i === cmdkActive ? 'cmdk-opt-active' : undefined" :class="['cmdk-opt', { active: i === cmdkActive }]"
             @click="$router.push(item.path); cmdkVisible = false" @mouseenter="cmdkActive = i">
          <span>{{ item.label }}</span>
          <span style="color: var(--text-secondary); font-size: 12px">{{ item.path }}</span>
        </div>
        <div v-if="!cmdkResults.length" style="color: var(--text-secondary); text-align: center; padding: 20px">{{ t('layout.noResults') }}</div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { QuestionFilled, DataBoard, DataAnalysis, Search, MagicStick, SetUp, Timer,
         TrendCharts, Collection, Monitor, Coin, VideoPlay, Odometer, Warning, CircleCheck,
         ScaleToOriginal, List, Setting, FolderOpened, Link, FirstAidKit, Lock,
         ChatDotRound, User, SwitchButton } from '@element-plus/icons-vue'
import { ref, computed, onMounted, onUnmounted, watch , provide } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getMe, getNotifications, ackAllNotifications, meOnce, resetMeCache } from '../api'
import api, { getStrategies, getFactorList } from '../api'
import { setLang, LANGUAGES } from '../i18n'
import Avatar from '../components/Avatar.vue'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const username = ref('')
const nickname = ref('')
const avatarUrl = ref('')
const role = ref('')
// W4 产出 0（白屏修复,wd-15 批四虚报落地）:loadPerms 实现——/auth/me → permissions/role 双回填
// 菜单由 perms 驱动(见 has());此前 L252 调用无定义,setup ReferenceError=登入白屏(盲审 A-P0)
const perms = ref([])
const has = k => perms.value.includes(k)
// W5:复用 router 的 meOnce(守卫已拉过则零二次请求);readonly 供页面写按钮灰(UI 提示层)
const navMap = ref({})
const loadPerms = async () => {
  try {
    const me = await meOnce()
    if (!me) return
    perms.value = me.permissions || []
    role.value = me.role || role.value
    navMap.value = me.nav || {}
  } catch {}
}
const navReadonly = computed(() => navMap.value[route.path.replace(/^\/+/, '').split('/')[0] || 'dashboard'] === 'readonly')
provide('navReadonly', navReadonly)
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
  await ensureRunbook()
  try {
    const r = await getNotifications('active', 20)
    // W1：runbook 预计算一行一次（原模板 4 处调用→0），旧通知 code=null→rb=null 不渲染
    notifs.value = (r.items || []).map(n => ({ ...n, rb: runbookOf(n.code) }))
    notifCount.value = r.count || 0
  } catch {}
}
const onAckAll = async () => {
  try { await ackAllNotifications(); await loadNotifs() } catch {}
}
// 类别 → 页面路由（点击通知直达）
// P3-2/P3-8：暗色+帮助抽屉+我的权限玻璃盒
// 主界面框架改版（2026-09-09 用户裁定）：侧栏平时隐藏,hover 热区向右展开/失焦左滑收起（覆盖式）——
// 折叠按钮/collapsed/localStorage/matchMedia 1706px 自动折叠全退役
// 2026-09-10 图钉：缺省钉死（推挤式常驻）；拔钉=hover 覆盖式。localStorage 记忆（'0'=未钉）
const railOpen = ref(false)
const railPinned = ref(localStorage.getItem('rail-pinned') !== '0')
const togglePin = () => {
  railPinned.value = !railPinned.value
  if (!railPinned.value) railOpen.value = false   // 拔钉即收回（浮动态缺省隐藏）
  localStorage.setItem('rail-pinned', railPinned.value ? '1' : '0')
}
const dark = ref(localStorage.getItem('theme-dark') === '1')
const onDark = v => { document.documentElement.classList.toggle('dark', v); localStorage.setItem('theme-dark', v ? '1' : '0') }
if (dark.value) document.documentElement.classList.add('dark')
const helpDrawer = ref(false)
const myPerms = ref([])
const showMyPerms = ref(false)
const myPermGroups = ref({ base: [], override: [], denied: [] })
const loadMyPerms = async () => {
  try {
    const me = await meOnce()
    myPerms.value = me.permissions || []
    const src = me.perm_sources || {}
    const denied = me.denied || []
    myPermGroups.value = {
      base: myPerms.value.filter(p => (src[p] || 'role-base') === 'role-base'),
      override: myPerms.value.filter(p => src[p] === 'user-override'),
      denied,
    }
  } catch {}
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
// runbook: web 长尾批 2026-09-01——通知表 code 字段已上(migration 0059),chip+一句话处置接线
// W3：runbook 后端单源——懒挂 loadNotifs 首载（bellVisible 门内，viewer 不白请求）；
// fetch 失败（如发布切换窗 404）下次 loadNotifs 懒补；无映射=chip 静默不渲染（降级同旧）
let runbookMap = null
const ensureRunbook = async () => {
  if (runbookMap) return
  try {
    const items = (await api.get('/runbook')).items
    if (items && Object.keys(items).length) runbookMap = items   // 空 200 不锁死,下轮懒补(盲审 A-P2)
  } catch {}
}
const runbookOf = code => (code && runbookMap && runbookMap[code]) || null 
import { goCategoryPath } from '../utils/goCategory'
const goCategory = c => router.push(goCategoryPath(c))   // wd-20 §2.6：映射抽 utils（与 Dashboard 共用）
loadHealth()
loadPerms()
onMounted(() => { loadNotifs(); notifTimer = setInterval(loadNotifs, 60000); loadDynamicIndex() })
onUnmounted(() => { if (notifTimer) clearInterval(notifTimer) })

const onLangChange = v => setLang(v)
const logout = async () => {
  try { await api.post('/auth/logout') } catch {}
  try { resetMeCache() } catch {}
  perms.value = []; navMap.value = {}; role.value = ''   // 盲审 A/B-P1:换人登录不串旧权限
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  router.push('/login')
}

// ⌘K 全局搜索
const cmdkVisible = ref(false)
const cmdkQuery = ref('')
const cmdkResults = ref([])
const cmdkActive = ref(0)          // roving index（wd-20 §2.6）
const cmdkInputRef = ref(null)
const focusCmdk = () => cmdkInputRef.value?.focus?.()
const onCmdkKeys = (e) => {
  const n = cmdkResults.value.length
  if (!n) return
  if (e.key === 'ArrowDown') { e.preventDefault(); cmdkActive.value = (cmdkActive.value + 1) % n }
  else if (e.key === 'ArrowUp') { e.preventDefault(); cmdkActive.value = (cmdkActive.value - 1 + n) % n }
  else if (e.key === 'Enter') { e.preventDefault(); const it = cmdkResults.value[cmdkActive.value]; if (it) { cmdkVisible.value = false; $routerPush(it.path) } }
  else if (e.key === 'Escape') { cmdkVisible.value = false }
}
const $routerPush = p => router.push(p)
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
// wd-20 §2.6 索引扩展：策略/因子/标的名（API 拉取，标签前缀区分；空态回落导航项）
const dynamicIndex = ref([])
const loadDynamicIndex = async () => {
  try {
    const [strategies, factors] = await Promise.all([getStrategies(), getFactorList()])
    dynamicIndex.value = [
      ...(strategies || []).map(s => ({ label: `▸ ${s.name}`, path: `/strategy` })),
      ...(factors?.items || factors || []).map(f => ({ label: `ƒ ${f.name}`, path: `/factors` })),
    ]
  } catch { dynamicIndex.value = [] }
}
const filterCmdk = () => {
  const q = cmdkQuery.value.toLowerCase()
  const pool = [...searchIndex, ...dynamicIndex.value]
  cmdkResults.value = q ? pool.filter(i => i.label.toLowerCase().includes(q) || i.path.includes(q)) : pool
  cmdkActive.value = 0
}
const onKeydown = (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); cmdkVisible.value = !cmdkVisible.value; filterCmdk() }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
/* 主界面框架改版（2026-09-09 用户裁定）：顶栏全宽+标题居左;侧栏到顶 hover 覆盖式。
   .aside-brand 用 el-header 组件（padding/高度=EP 默认,与顶栏同源）;去 .el-menu 自带 border-right 消 1px 宽差 */
.app-title { font-size: 18px; font-weight: 700; color: var(--brand-600); }
.aside-brand { display: flex; align-items: center; gap: 8px; }
.aside-brand-text { color: #fff; font-size: 18px; font-weight: 700; }
/* 图钉（2026-09-10）：标题后右对齐——flex 布局 text 撑开+margin-left:auto；钉死=竖直针高亮,浮动=斜 45°灰 */
.rail-pin { margin-left: auto; display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 6px; border: none; cursor: pointer;
  background: transparent; color: var(--text-secondary, #8a94a6); transition: color .15s, background .15s; }
.rail-pin:hover { color: #fff; background: rgba(255, 255, 255, .12); }
.rail-pin.active { color: #fff; }
.overlay-aside .el-menu { border-right: none; }   /* EP 默认 1px 右边框→与标题区宽度差 1px,去掉 */
.rail-hotzone { position: fixed; left: 0; top: 0; bottom: 0; width: 12px; z-index: 1800; }
.rail-hotzone::after {
  /* 把手指示：左缘可划出菜单的视觉暗示（平时侧栏完全隐藏,无指示=不可发现） */
  content: ''; position: absolute; right: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 48px; border-radius: 2px;
  background: var(--text-secondary); opacity: .35;
}
.overlay-aside {
  position: fixed; left: 0; top: 0; bottom: 0;      /* 到顶：滑出时连顶栏左段一并遮盖 */
  background: var(--bg-sidebar);   /* 2026-09-10 四轮裁定：恢复深底（同色一体方案撤回） */
  transform: translateX(-100%);            /* 平时隐藏,左滑出视口 */
  visibility: hidden;                      /* 盲审 P1：出 Tab 焦点序（仅 transform 出视口时键盘 Tab 仍会聚焦不可见菜单） */
  transition: transform .25s ease, visibility .25s;   /* visibility 离散：收起=滑完再隐,展开=立即可见,动画不受影响 */
  z-index: 1801;                            /* 盖住热区(1800)与顶栏(el-header 无定位),防开↔合抖动。EP 弹层 2000+ 之下/页面 --z-sticky 100 之上——令牌化挂 web backlog（盲审 P2） */
  overflow-y: auto;
  display: flex; flex-direction: column;   /* 菜单 flex:1 内滚,图钉贴底（2026-09-10 三轮） */
}
.overlay-aside .el-menu { flex: 1; }
.overlay-aside.open { transform: translateX(0); visibility: visible; box-shadow: 4px 0 16px rgba(0, 0, 0, .18); }
/* 图钉钉死态（2026-09-10 四轮）：fixed 常驻到顶（左列全高）——顶栏/工作区经 .pinned-shift margin 让位缩窄 */
.overlay-aside.pinned { transform: none; visibility: visible; box-shadow: none; }
.pinned-shift { margin-left: 200px; }   /* 顶栏+inner 容器同让位（推挤式缩范围） */

/* 通知级别色点：critical 深红 / warn 橙 / info 灰（走 tokens：--critical/--warn-fill/--text-secondary） */
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.dot.critical { background: var(--critical); }
.dot.warn { background: var(--warn-fill); }
.dot.info { background: var(--text-secondary); }
.notif-body { white-space: pre-wrap; color: var(--text-secondary); font-size: 12px; line-height: 1.5; margin: 4px 0 2px 14px; max-height: 4.5em; overflow: hidden; }
.notif-guide { color: var(--el-color-primary); font-size: 12px; line-height: 1.5; margin: 2px 0 2px 14px; }
/* wd-20 §2.6 ⌘K combobox 选项 */
.cmdk-opt { padding: var(--sp-2) 12px; cursor: pointer; border-bottom: 1px solid var(--border-weak);
  display: flex; justify-content: space-between; }
.cmdk-opt.active { background: var(--el-fill-color-light); }
.cmdk-opt.active span:first-child { color: var(--brand-600); font-weight: 600; }
</style>
