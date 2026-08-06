<template>
  <el-dialog v-model="visible" :title="title" width="90%" top="3vh" @open="onOpen" @closed="onClose" destroy-on-close>
    <div style="display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; align-items: center">
      <el-radio-group v-model="period" size="small" @change="onPeriodChange">
        <el-radio-button label="日K" />
        <el-radio-button label="周K" />
        <el-radio-button label="月K" />
      </el-radio-group>
      <el-select v-model="mainIndicator" size="small" placeholder="主图指标" style="width: 140px" @change="onMainChange">
        <el-option label="无" value="" />
        <el-option label="MA" value="MA" />
        <el-option label="BOLL" value="BOLL" />
        <el-option label="SAR" value="SAR" />
      </el-select>
      <el-select v-model="subIndicator" size="small" placeholder="副图指标" style="width: 140px" @change="onSubChange">
        <el-option label="无" value="" />
        <el-option label="VOL" value="VOL" />
        <el-option label="MACD" value="MACD" />
        <el-option label="KDJ" value="KDJ" />
        <el-option label="RSI" value="RSI" />
        <el-option label="WR" value="WR" />
        <el-option label="CCI" value="CCI" />
        <el-option label="BIAS" value="BIAS" />
        <el-option label="OBV" value="OBV" />
      </el-select>
      <span style="color: #999; font-size: 12px">{{ symbol }} · {{ dataCount }} 根K线</span>
    </div>
    <div ref="chartRef" style="width: 100%; height: 500px"></div>
  </el-dialog>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { init, dispose } from 'klinecharts'
import api from '../api'

const props = defineProps({
  modelValue: Boolean,
  symbol: String,
  name: String,
})
const emit = defineEmits(['update:modelValue'])

const visible = ref(props.modelValue)
watch(() => props.modelValue, v => { visible.value = v })
watch(visible, v => emit('update:modelValue', v))

const chartRef = ref(null)
const title = ref('')
const period = ref('日K')
const mainIndicator = ref('MA')
const subIndicator = ref('VOL')
const dataCount = ref(0)
let chart = null
let allData = []

const onOpen = async () => {
  title.value = `${props.name || props.symbol} · K线图`
  await nextTick()
  await loadChart()
}

const onClose = () => {
  if (chart) { dispose(chart); chart = null }
}

// K线聚合
function aggregateKline(data, n) {
  if (n <= 1) return data
  const result = []
  for (let i = 0; i < data.length; i += n) {
    const batch = data.slice(i, i + n)
    if (!batch.length) break
    result.push({
      timestamp: batch[0].timestamp,
      open: batch[0].open,
      high: Math.max(...batch.map(d => d.high)),
      low: Math.min(...batch.map(d => d.low)),
      close: batch[batch.length - 1].close,
      volume: batch.reduce((s, d) => s + (d.volume || 0), 0),
    })
  }
  return result
}

async function loadChart() {
  if (!chartRef.value) return

  // 初始化图表
  chart = init(chartRef.value, {
    styles: {
      candle: {
        type: 'candle_solid',
        bar: {
          upColor: '#ef5350',      // 涨红
          downColor: '#26a69a',    // 跌绿
          noChangeColor: '#888888',
        },
      },
    },
  })
  if (!chart) return

  // 拉数据：不传 days 走后端默认(全部历史)
  try {
    const raw = await api.get(`/kline/${props.symbol}`)
    allData = raw.map(d => ({
      timestamp: new Date(d.ts).getTime(),
      open: d.open, high: d.high, low: d.low, close: d.close, volume: d.volume,
    }))
  } catch { allData = [] }

  // 设置数据加载器
  const periodConfig = getPeriodConfig()
  chart.setSymbol({ ticker: props.symbol, pricePrecision: 2, volumePrecision: 0 })
  chart.setPeriod(periodConfig)

  chart.setDataLoader({
    getBars({ type, callback }) {
      let data = getPeriodData()
      dataCount.value = data.length
      if (type === 'init') {
        callback(data, false)
      } else {
        callback([], false)
      }
    },
  })

  // 主图指标
  if (mainIndicator.value) {
    chart.createIndicator(mainIndicator.value, true)
  }
  // 副图指标
  if (subIndicator.value) {
    chart.createIndicator(subIndicator.value, false)
  }
}

function getPeriodConfig() {
  if (period.value === '周K') return { type: 'week', span: 1 }
  if (period.value === '月K') return { type: 'month', span: 1 }
  return { type: 'day', span: 1 }
}

function getPeriodData() {
  if (period.value === '周K') return aggregateKline(allData, 5)
  if (period.value === '月K') return aggregateKline(allData, 20)
  return allData
}

function onPeriodChange() {
  if (!chart) return
  chart.setPeriod(getPeriodConfig())
  // 重新触发数据加载
  chart.setDataLoader({
    getBars({ type, callback }) {
      let data = getPeriodData()
      dataCount.value = data.length
      if (type === 'init') callback(data, false)
      else callback([], false)
    },
  })
}

function onMainChange() {
  if (!chart) return
  // 移除旧主图指标
  chart.removeIndicator({ name: 'MA' })
  chart.removeIndicator({ name: 'BOLL' })
  chart.removeIndicator({ name: 'SAR' })
  if (mainIndicator.value) {
    chart.createIndicator(mainIndicator.value, true)
  }
}

function onSubChange() {
  if (!chart) return
  // 移除所有副图指标
  chart.removeIndicator({ name: 'VOL' })
  chart.removeIndicator({ name: 'MACD' })
  chart.removeIndicator({ name: 'KDJ' })
  chart.removeIndicator({ name: 'RSI' })
  chart.removeIndicator({ name: 'WR' })
  chart.removeIndicator({ name: 'CCI' })
  chart.removeIndicator({ name: 'BIAS' })
  chart.removeIndicator({ name: 'OBV' })
  if (subIndicator.value) {
    chart.createIndicator(subIndicator.value, false)
  }
}
</script>
