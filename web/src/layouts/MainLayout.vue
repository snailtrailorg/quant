<template>
  <el-container style="height: 100vh">
    <!-- 主界面框架改版（2026-09-09 用户裁定）：顶栏全宽顶到最左 + 标题居左；侧栏移出文档流改 hover 覆盖式 -->
    <el-header height="48px" :class="{ 'pinned-shift': railPinned }" style="background: var(--bg-surface); border-bottom: 1px solid var(--border-weak); display: flex; align-items: center; justify-content: space-between" @mouseenter="railOpen = false">
      <div style="display: flex; align-items: center; gap: 12px">
        <!-- 钉死态隐藏（2026-09-10 四轮裁定）：侧栏 title 到顶顶替,避免双标题 -->
        <span v-show="!railPinned" class="app-title">{{ t('app.title') }}</span>
      </div>
      <div style="display: flex; align-items: center; gap: 24px">
          <!-- 批21 标题栏圆钮统一（2026-09-14 用户五裁定）：功能分组 + 全站圆钮统一 32px（IconBtn）；组内 gap 16px、组间 gap 24px -->
          <!-- ═ 安全组 ═ -->
          <!-- P1-4（05 §5.2 要点 9）：⛔ 急停常驻顶栏（火警时不该先找消防栓在几楼） -->
          <IconBtn type="danger" :icon="SwitchButton" :title="t('risk.halt')" @click="onEmergencyHalt" />

          <!-- ═ 告警（批22：admin-only，角标=活跃告警数，点击进系统监控页） ═ -->
          <el-badge v-if="role === 'admin'" :value="alertCount" :hidden="!alertCount" :max="99"
                    :type="alertLevel === 'critical' ? 'danger' : 'warning'">
            <IconBtn :icon="Bell" :title="t('sysmon.title')" @click="$router.push('/observe')" />
          </el-badge>

          <!-- ═ 偏好组（语言/暗色乒乓，恢复到标题栏） ═ -->
          <div style="display: flex; align-items: center; gap: 16px">
            <IconBtn :title="langTitle" @click="toggleLang">{{ langLabel }}</IconBtn>
            <IconBtn :icon="dark ? Sunny : Moon" :title="dark ? t('layout.toLight') : t('layout.toDark')" @click="onDark(!dark)" />
          </div>

          <!-- ═ 用户区（32px 头像圆钮触发下拉，最小暴露不显昵称） ═ -->
          <el-dropdown trigger="click" @command="onSystemCommand">
            <IconBtn :title="t('profile.title')">
              <Avatar :url="avatarUrl" :name="nickname || username" size="icon" />
            </IconBtn>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile"><el-icon><User /></el-icon>{{ t('profile.title') }}</el-dropdown-item>
                <el-dropdown-item command="ai"><el-icon><ChatDotRound /></el-icon>{{ t('nav.aiChat') }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided><el-icon><Back /></el-icon>{{ t('user.logout') }}</el-dropdown-item>
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
        <el-header class="aside-brand" height="48px">
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
            <el-menu-item v-if="has('system_config')" index="/routing"><el-icon><Guide /></el-icon>{{ t('nav.dataRouting') }}</el-menu-item>
            <el-menu-item v-if="has('system_config')" index="/perm-resources"><el-icon><Grid /></el-icon>{{ t('nav.permResources') }}</el-menu-item>
            <el-menu-item v-if="has('system_config')" index="/settings"><el-icon><Tools /></el-icon>{{ t('nav.settings') }}</el-menu-item>   <!-- 批37：系统设置置底 -->
          </el-sub-menu>
        </el-menu>

      </el-aside>
      </el-container>
    </el-container>
    <!-- 批16 五批：个人中心弹窗唯一入口（v-if 挂载——关闭即卸载，Profile 内清理链随 onUnmounted 走） -->
    <Profile v-if="profileDlg" :initial-tab="profileTab" @close="onProfileClosed" @updated="onProfileUpdated" />
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
          <span style="color: var(--text-secondary); font-size: var(--fs-foot)">{{ item.path }}</span>
        </div>
        <div v-if="!cmdkResults.length" style="color: var(--text-secondary); text-align: center; padding: 20px">{{ t('layout.noResults') }}</div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { DataBoard, DataAnalysis, Search, MagicStick, SetUp, Timer,
         TrendCharts, Collection, Monitor, Coin, VideoPlay, Odometer, Warning, CircleCheck,
         ScaleToOriginal, List, Setting, FolderOpened, Link, FirstAidKit, Lock,
         ChatDotRound, Bell, User, SwitchButton, Back, Grid, Guide } from '@element-plus/icons-vue'
import { ref, computed, onMounted, onUnmounted, watch , provide } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getMe, meOnce, resetMeCache, getSystemAlerts } from '../api'
import api, { getStrategies, getFactorList } from '../api'
import { setLang } from '../i18n'
import Avatar from '../components/Avatar.vue'
import IconBtn from '../components/IconBtn.vue'
import Profile from '../views/Profile.vue'

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
    navAliases.value = me.nav_aliases || {}   // 批33b：别名随 me（守卫同源）
    role.value = me.role || role.value
    navMap.value = me.nav || {}
  } catch {}
}
// 批27-18（全局检视 B-P2 面修）：路由首段 ≠ NAV_ITEMS 键的子页全部补映射——原只按首段直查，
// data-manage/:syncId（SymbolManage）等子页 readonly 守卫失效（首段 'data-manage' 查 navMap 恒 undefined）
// 批33b P0-2：别名表动态化——真源 GET /auth/me 的 nav_aliases（注册表下发；本地硬编码表退役）。
// me 未回时空表=行为等同现状（fail-open 先例）。
const navAliases = ref({})
const navReadonly = computed(() => {
  const seg = route.path.replace(/^\/+/, '').split('/')[0] || 'dashboard'
  return navMap.value[navAliases.value[seg] || seg] === 'readonly'
})
provide('navReadonly', navReadonly)

getMe().then(me => { username.value = me.username; role.value = me.role; nickname.value = me.nickname || ''; avatarUrl.value = me.avatar_url || '' }).catch(e => { console.error(e); username.value = ''; role.value = '' })
// 用户下拉（批21：头像触发；个人中心/AI/退出）
const onSystemCommand = (cmd) => {
  if (cmd === 'profile') { profileTab.value = 'basic'; profileDlg.value = true }
  else if (cmd === 'ai') router.push('/chat')
  else if (cmd === 'logout') logout()
}

// 个人中心弹窗：头像下拉直开 + 深链 /?profile=<tab>（redirect 链 /profile /im-bots /feishu 收口于此）
const profileDlg = ref(false)
const profileTab = ref('basic')
let openedByQuery = false   // 盲审A-P2-10：深链开的弹窗随 query 消失而关（后退语义）；下拉开的与 URL 无关不受牵连
watch(() => route.query.profile, v => {
  if (v && ['basic', 'im', 'pwd', 'perms'].includes(v)) {   // 批20：第四权限 tab 入深链白名单
    profileTab.value = v; profileDlg.value = true; openedByQuery = true
  } else if (!v && openedByQuery) {
    profileDlg.value = false; openedByQuery = false
  }
}, { immediate: true })   // immediate：直链落地/刷新即开
const onProfileClosed = () => {
  profileDlg.value = false; openedByQuery = false
  if (route.query.profile) router.replace({ query: { ...route.query, profile: undefined } })   // 关后清参：刷新/后退不再重开
}
const onProfileUpdated = (u) => {
  if (u?.nickname != null) nickname.value = u.nickname
  if (u?.avatar_url != null) avatarUrl.value = u.avatar_url
}

// ——— 告警角标（批22：活跃告警数，30s 轮询；admin-only） ———
const alertCount = ref(0)
const alertLevel = ref('warning')
let alertTimer = null
const loadAlerts = async () => {
  if (role.value !== 'admin') return
  try {
    const r = await getSystemAlerts()
    const items = r.items || []
    alertCount.value = items.length
    alertLevel.value = items.some(x => x.severity === 'critical') ? 'critical' : 'warning'
  } catch {}
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
const onDark = v => { dark.value = v; document.documentElement.classList.toggle('dark', v); localStorage.setItem('theme-dark', v ? '1' : '0') }
if (dark.value) document.documentElement.classList.add('dark')
import { Moon, Sunny } from '@element-plus/icons-vue'

// P1-4：急停（熔断=轻确认,04 §4.5——所有可登录角色可触发,后端 require_perm 兜底）
const onEmergencyHalt = async () => {
  try {
    await ElMessageBox.confirm(t('risk.confirmHalt'), t('common.confirm'), { type: 'warning' })
    const { riskHalt } = await import('../api')
    await riskHalt(); ElMessage.success(t('risk.halted'))
  } catch (e) { if (e?.response) ElMessage.error(String(e)) }
}
// 批22：告警角标初始化 + 30s 轮询（健康/通知逻辑迁至系统监控页与 NotificationList 组件）
loadPerms()
onMounted(() => { loadAlerts(); alertTimer = setInterval(loadAlerts, 30000); loadDynamicIndex() })
onUnmounted(() => { if (alertTimer) clearInterval(alertTimer) })

// 批21：语言乒乓（真源 locale.value）；批48 后用户裁定：按钮显示**当前语言**（中文态"中"/英文态"EN"），title 仍指目标语言
const langLabel = computed(() => locale.value === 'zh' ? '中' : 'EN')
const langTitle = computed(() => locale.value === 'zh' ? t('layout.toEn') : t('layout.toZh'))
const toggleLang = () => setLang(locale.value === 'zh' ? 'en' : 'zh')
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
// 批27-25：改 computed + t()——原模块级 const 硬编码中文，en 语言下 ⌘K 索引仍中文且不随语言变
const searchIndex = computed(() => [
  { label: t('nav.screener'), path: '/screener' }, { label: t('nav.pool'), path: '/pool' },
  { label: t('nav.factors'), path: '/factors' }, { label: t('nav.strategy'), path: '/strategy' },
  { label: t('nav.backtest'), path: '/backtest' }, { label: t('nav.analysis'), path: '/analysis' },
  { label: t('nav.liveTasks'), path: '/live-task' }, { label: t('nav.tradingDesk'), path: '/trading' },
  { label: t('nav.risk'), path: '/risk' }, { label: t('nav.reconcile'), path: '/reconcile' },
  { label: t('nav.dataops'), path: '/dataops' }, { label: t('nav.integrations'), path: '/integrations' },
  { label: t('nav.observe'), path: '/observe' }, { label: t('nav.settings'), path: '/settings' },
  { label: t('nav.chat'), path: '/chat' },
])
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
  const pool = [...searchIndex.value, ...dynamicIndex.value]   // 批27-25：computed 消费 .value
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
.app-title { font-size: var(--fs-page); font-weight: 700; color: var(--brand-600); }
.aside-brand { display: flex; align-items: center; gap: 8px; }
.aside-brand-text { color: #fff; font-size: var(--fs-page); font-weight: 700; }
/* 图钉（2026-09-10）：标题后右对齐——flex 布局 text 撑开+margin-left:auto；钉死=竖直针高亮,浮动=斜 45°灰 */
.rail-pin { margin-left: auto; display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 6px; border: none; cursor: pointer;
  background: transparent; color: var(--text-secondary, #8a94a6); transition: color .15s, background .15s; }
.rail-pin:hover { color: #fff; background: rgba(255, 255, 255, .12); }
.rail-pin.active { color: #fff; }
.overlay-aside .el-menu { border-right: none; }   /* EP 默认 1px 右边框→与标题区宽度差 1px,去掉 */
.rail-hotzone { position: fixed; left: 0; top: 0; bottom: 0; width: 12px; z-index: var(--z-rail); }
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
  z-index: var(--z-rail-aside);                  /* 盖住热区(--z-rail)与顶栏(el-header 无定位),防开↔合抖动。EP 弹层 2000+ 之下/页面 --z-sticky 100 之上（批26-2 令牌化） */
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
.notif-body { white-space: pre-wrap; color: var(--text-secondary); font-size: var(--fs-foot); line-height: 1.5; margin: 4px 0 2px 14px; max-height: 4.5em; overflow: hidden; }
.notif-guide { color: var(--el-color-primary); font-size: var(--fs-foot); line-height: 1.5; margin: 2px 0 2px 14px; }
/* wd-20 §2.6 ⌘K combobox 选项 */
.cmdk-opt { padding: var(--sp-2) 12px; cursor: pointer; border-bottom: 1px solid var(--border-weak);
  display: flex; justify-content: space-between; }
.cmdk-opt.active { background: var(--el-fill-color-light); }
.cmdk-opt.active span:first-child { color: var(--brand-600); font-weight: 600; }
</style>
