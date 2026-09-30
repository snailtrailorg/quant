<template>
  <!-- 批 83a：集成中心「交易账号」页签（下单/行情侧）——与 DataSources.vue 同构薄壳，
       表格+弹窗+能力筛选归 InterfacesCard（kind 参数化）。
       本页保留特有件：API 密钥账户卡（P4-5 accounts 表，实盘任务绑定用；批55b 盲审 A-P1
       曾修「写入口断链」，拆表后随账号语义从原单列表页迁到本页，功能不丢）。 -->
  <div>
    <InterfacesCard kind="trading_account" />
    <AccountsCard />
  </div>
</template>

<script setup>
import InterfacesCard from '../components/InterfacesCard.vue'
import AccountsCard from '../components/AccountsCard.vue'
</script>
