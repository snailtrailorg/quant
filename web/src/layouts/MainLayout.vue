<template>
  <el-container style="height: 100vh">
    <el-aside width="220px" style="background: #304156">
      <div style="color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center">
        {{ t('app.title') }}
      </div>
      <el-menu :default-active="route.path" router background-color="#304156" text-color="#bfcbd9" active-text-color="#409EFF">
        <!-- 首页 -->
        <el-menu-item index="/"><el-icon><DataLine /></el-icon>总览</el-menu-item>

        <!-- 交易工作台 -->
        <el-sub-menu index="trade">
          <template #title><el-icon><Money /></el-icon>交易工作台</template>
          <el-menu-item index="/trading"><el-icon><Wallet /></el-icon>实盘看板</el-menu-item>
          <el-menu-item index="/monitoring"><el-icon><Monitor /></el-icon>实时监控</el-menu-item>
          <el-menu-item index="/tasks"><el-icon><List /></el-icon>后台任务</el-menu-item>
        </el-sub-menu>

        <!-- 策略实验室 -->
        <el-sub-menu index="strategy">
          <template #title><el-icon><Setting /></el-icon>策略实验室</template>
          <el-menu-item index="/strategy"><el-icon><List /></el-icon>策略管理</el-menu-item>
          <el-menu-item index="/backtest"><el-icon><Histogram /></el-icon>回测中心</el-menu-item>
          <el-menu-item index="/pool"><el-icon><FolderOpened /></el-icon>标的池</el-menu-item>
          <el-menu-item index="/factors"><el-icon><MagicStick /></el-icon>因子库</el-menu-item>
        </el-sub-menu>

        <!-- 数据分析 -->
        <el-sub-menu index="analysis">
          <template #title><el-icon><TrendCharts /></el-icon>数据分析</template>
          <el-menu-item index="/ascreen"><el-icon><Search /></el-icon>A股筛选</el-menu-item>
          <el-menu-item index="/cbscreen"><el-icon><Coin /></el-icon>可转债筛选</el-menu-item>
          <el-menu-item index="/etfscreen"><el-icon><Histogram /></el-icon>ETF筛选</el-menu-item>
          <el-menu-item index="/analysis"><el-icon><TrendCharts /></el-icon>A股分析</el-menu-item>
          <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon>AI助手</el-menu-item>
        </el-sub-menu>

        <!-- 风控 -->
        <el-sub-menu index="risk">
          <template #title><el-icon><Warning /></el-icon>风控</template>
          <el-menu-item index="/risk"><el-icon><Shield /></el-icon>风控中心</el-menu-item>
          <el-menu-item index="/reconcile"><el-icon><ScaleToOriginal /></el-icon>对账报告</el-menu-item>
          <el-menu-item index="/risk-rules"><el-icon><Setting /></el-icon>风控规则</el-menu-item>
        </el-sub-menu>

        <!-- 系统运维（Admin + Analyst 可见，Trader/Viewer 不可见） -->
        <el-sub-menu index="system" v-if="['admin', 'analyst'].includes(role)">
          <template #title><el-icon><Tools /></el-icon>系统运维</template>
          <el-menu-item index="/data-manage"><el-icon><Download /></el-icon>数据同步</el-menu-item>
          <el-menu-item index="/health"><el-icon><FirstAidKit /></el-icon>健康监控</el-menu-item>
          <el-menu-item index="/logs"><el-icon><Document /></el-icon>日志告警</el-menu-item>
        </el-sub-menu>

        <!-- 账户设置（仅 Admin） -->
        <el-sub-menu index="account-group" v-if="role === 'admin'">
          <template #title><el-icon><User /></el-icon>系统设置</template>
          <el-menu-item index="/account"><el-icon><Key /></el-icon>账户管理</el-menu-item>
          <el-menu-item index="/audit"><el-icon><Tickets /></el-icon>审计日志</el-menu-item>
          <el-menu-item index="/llm-models"><el-icon><ChatDotRound /></el-icon>AI 模型</el-menu-item>
          <el-menu-item index="/feishu"><el-icon><ChatDotRound /></el-icon>飞书机器人</el-menu-item>
          <el-menu-item index="/data-sources"><el-icon><Connection /></el-icon>数据源</el-menu-item>
          <el-menu-item index="/channels"><el-icon><ChatDotRound /></el-icon>消息通道</el-menu-item>
          <el-menu-item index="/brokers"><el-icon><Wallet /></el-icon>交易通道</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header style="background: #fff; border-bottom: 1px solid #eee; display: flex; align-items: center; justify-content: space-between">
        <div></div>
        <div style="display: flex; align-items: center; gap: 16px">
          <el-select v-model="lang" @change="onLangChange" size="small" style="width: 100px">
            <el-option label="中文" value="zh" />
            <el-option label="English" value="en" />
          </el-select>
          <el-tag>{{ username }} ({{ role }})</el-tag>
          <el-button size="small" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
      <el-footer style="background: #f5f7fa; border-top: 1px solid #eee; height: auto; padding: 0">
        <Footer />
      </el-footer>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getMe } from '../api'
import Footer from '../components/Footer.vue'
import { setLang } from '../i18n'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const username = ref('')
const role = ref('')
const lang = ref(locale.value)

getMe().then(me => { username.value = me.username; role.value = me.role })

const onLangChange = v => setLang(v)
const logout = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('role')
  router.push('/login')
}
</script>
