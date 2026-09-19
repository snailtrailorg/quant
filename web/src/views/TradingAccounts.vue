<template>
  <!-- 批55b:交易账户页签=外部接口统一表·交易视图(cap∋trading,TradingAccounts 全重写——27 号执行注记)。
      第二卡=API 密钥账户(实盘任务绑定专用,盲审 A-P1 修:写入口保留不断链;/api/account 端点不动)。 -->
  <div>
    <InterfacesCard view="trading" />
    <AccountsCard />
  </div>
</template>

<script setup>
import InterfacesCard from '../components/InterfacesCard.vue'
import AccountsCard from '../components/AccountsCard.vue'
</script>
