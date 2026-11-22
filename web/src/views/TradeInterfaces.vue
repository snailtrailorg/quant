<template>
  <div>
    <InterfaceStatusCard kind="td" />
    <InterfacesManager kind="td" />
    <AccountsCard />
  </div>
</template>

<script setup>
import InterfaceStatusCard from '../components/InterfaceStatusCard.vue'
import InterfacesManager from '../components/InterfacesManager.vue'
import AccountsCard from '../components/AccountsCard.vue'
</script>
