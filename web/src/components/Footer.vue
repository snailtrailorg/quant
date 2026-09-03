<template>
  <div class="icp-footer">
    <a href="https://beian.miit.gov.cn" target="_blank" rel="noopener">粤ICP备2026111649号</a>
  </div>
</template>

<script setup>
</script>

<style scoped>
.icp-footer {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 22px;
  line-height: 22px;
  text-align: center;
  font-size: 12px;
  background: #f5f7fa;
  border-top: 1px solid #eaecef;
  z-index: 100;
}
.icp-footer a {
  color: var(--text-secondary);
  text-decoration: none;
}
</style>
