<template>
  <el-card>
    <template #header>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span>实盘交易看板</span>
        <el-button @click="load" size="small">刷新</el-button>
      </div>
    </template>
    <el-row :gutter="20" style="margin-bottom: 20px">
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总资产</div><div class="value">¥0</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">今日盈亏</div><div class="value">¥0</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">总盈亏</div><div class="value">¥0</div></div></el-card></el-col>
      <el-col :span="6"><el-card shadow="hover"><div class="stat"><div class="label">持仓数</div><div class="value">0</div></div></el-card></el-col>
    </el-row>
    <el-tabs>
      <el-tab-pane label="持仓">
        <el-table :data="positions" stripe>
          <el-table-column prop="symbol" label="标的" width="120" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="avg_price" label="均价" width="100" />
          <el-table-column prop="last_price" label="现价" width="100" />
          <el-table-column prop="pnl" label="盈亏" width="100" />
          <el-table-column prop="direction" label="方向" width="80" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="订单">
        <el-table :data="orders" stripe>
          <el-table-column prop="ts" label="时间" width="160" />
          <el-table-column prop="symbol" label="标的" width="120" />
          <el-table-column prop="action" label="方向" width="80" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="price" label="价格" width="100" />
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="盈亏曲线">
        <div style="height: 400px; display: flex; align-items: center; justify-content: center; color: #999">
          实盘开始后显示
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { getPosition, getOrders } from '../api'
const positions = ref([])
const orders = ref([])
const load = async () => {
  try { positions.value = (await getPosition()).positions || [] } catch {}
  try { orders.value = (await getOrders()).orders || [] } catch {}
}
onMounted(load)
</script>
<style scoped>
.stat { text-align: center; padding: 12px 0; }
.stat .label { color: #909399; font-size: 13px; }
.stat .value { font-size: 24px; font-weight: bold; color: #303133; margin-top: 4px; }
</style>