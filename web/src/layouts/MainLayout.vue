<template>
  <el-container style="height: 100vh">
    <el-aside width="220px" style="background: #304156">
      <div style="color: #fff; padding: 20px; font-size: 18px; font-weight: bold; text-align: center">
        {{ t('app.title') }}
      </div>
      <el-menu :default-active="route.path" router background-color="#304156" text-color="#bfcbd9" active-text-color="#409EFF">
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
          <el-menu-item index="/logs"><el-icon><Document /></el-icon>{{ t('nav.logs') }}</el-menu-item>
        </el-sub-menu>

        <!-- 账户设置（仅 Admin） -->
        <el-sub-menu index="account-group" v-if="role === 'admin'">
          <template #title><el-icon><User /></el-icon>{{ t('nav.settings') }}</template>
          <el-menu-item index="/account"><el-icon><Key /></el-icon>{{ t('nav.account') }}</el-menu-item>
          <el-menu-item index="/audit"><el-icon><Tickets /></el-icon>{{ t('nav.audit') }}</el-menu-item>
          <el-menu-item index="/llm-models"><el-icon><ChatDotRound /></el-icon>{{ t('nav.llmModels') }}</el-menu-item>
          <el-menu-item index="/feishu"><el-icon><ChatDotRound /></el-icon>{{ t('nav.feishu') }}</el-menu-item>
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
