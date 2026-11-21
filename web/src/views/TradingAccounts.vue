<template>
  <!-- M6a：接口管理两页签（MD/TD，code 身份新 API）；旧 InterfacesCard(旧 /trading-accounts int) 交易族退役。
       AccountsCard（P4-5 API 密钥账户）保留。 -->
  <div>
    <InterfacesManager />
    <AccountsCard />
  </div>
</template>

<script setup>
import InterfacesManager from '../components/InterfacesManager.vue'
import AccountsCard from '../components/AccountsCard.vue'
</script>
