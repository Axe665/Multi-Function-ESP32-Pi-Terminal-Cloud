<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { useTheme } from '../lib/theme'

const props = defineProps({
  option: { type: Object, default: () => ({}) },
  height: { type: String, default: '280px' },
})

const host = ref(null)
const { theme } = useTheme()
let chart = null
let observer = null

function apply() {
  if (!chart) return
  const base = props.option || {}
  // tooltip 是 ECharts 自己画的，不读 CSS 变量，这里按主题补一套
  const dark = theme.value === 'dark'
  const option = base.tooltip
    ? { ...base, tooltip: {
        backgroundColor: dark ? '#282b25' : '#ffffff',
        borderColor: dark ? '#43483e' : '#e8eae5',
        textStyle: { color: dark ? '#e3e3dc' : '#1a1c18' },
        ...base.tooltip,
      } }
    : base
  chart.setOption(option, { notMerge: true })
}

onMounted(() => {
  chart = echarts.init(host.value, null, { renderer: 'canvas' })
  apply()
  observer = new ResizeObserver(() => chart && chart.resize())
  observer.observe(host.value)
})

watch(() => props.option, apply, { deep: true })

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="host" :style="{ height, width: '100%' }" role="img" aria-label="图表"></div>
</template>
