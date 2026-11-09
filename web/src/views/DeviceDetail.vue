<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, fmtDay, fmtTime } from '../api'
import PageHeader from '../components/PageHeader.vue'
import ChartBox from '../components/ChartBox.vue'
import 'echarts-wordcloud'

const route = useRoute()
const device = ref(null)
const meta = ref({ metrics: [] })
const metric = ref('')
const series = ref(null)
const heat = ref(null)
const words = ref([])
const advice = ref([])
const error = ref('')

const id = computed(() => route.params.id)

const label = (key) => meta.value.metrics.find((m) => m.key === key)?.label || key
const unit = (key) => meta.value.metrics.find((m) => m.key === key)?.unit || ''

async function load() {
  try {
    const [devs, m] = await Promise.all([api('/devices'), api('/meta')])
    meta.value = m
    device.value = devs.find((d) => d.device_id === id.value) || null
    if (!device.value) throw new Error('设备不存在')
    metric.value = device.value.metrics[0] || m.metrics[0]?.key || ''
    const [a] = await Promise.all([api(`/advice?edge=${encodeURIComponent(device.value.edge_id)}&limit=200`).catch(() => [])])
    advice.value = a.filter((x) => x.device_id === id.value).slice(0, 10)
    await loadCharts()
  } catch (e) {
    error.value = e.message
  }
}

async function loadCharts() {
  if (!device.value || !metric.value) return
  const q = `metric=${encodeURIComponent(metric.value)}&edge=${encodeURIComponent(device.value.edge_id)}&device=${encodeURIComponent(id.value)}`
  ;[series.value, heat.value] = await Promise.all([
    api(`/series?${q}`).catch(() => null),
    api(`/heatmap?${q}`).catch(() => null),
  ])
  const w = await api(`/wordcloud?days=7&scope=${encodeURIComponent(device.value.edge_id)}`).catch(() => null)
  words.value = w?.words || []
}

onMounted(load)
watch(id, load)
watch(metric, loadCharts)

const seriesOption = computed(() => {
  const pts = series.value?.points || []
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: pts.map((p) => fmtDay(p.day)),
      axisLine: { lineStyle: { color: '#c3c8bb' } },
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: 'value', scale: true,
      splitLine: { lineStyle: { color: '#e3e6dc' } },
      axisLabel: { fontSize: 11 },
    },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      lineStyle: { width: 2, color: '#386a20' },
      areaStyle: { color: 'rgba(56,106,32,0.10)' },
      data: pts.map((p) => p.mean),
    }],
  }
})

const heatOption = computed(() => {
  // 云端只有日聚合，所以热力图纵轴是单行、横轴是日期（没有 7×24 的原始时段）
  const cells = heat.value?.cells || []
  const data = cells.map((c, i) => [i, 0, Number(c.mean.toFixed(1))])
  const values = cells.map((c) => c.mean)
  const lo = values.length ? Math.min(...values) : 0
  const hi = values.length ? Math.max(...values) : 1
  return {
    grid: { left: 8, right: 8, top: 8, bottom: 32, containLabel: true },
    tooltip: { formatter: (p) => `${fmtDay(cells[p.value[0]]?.day)}<br/>${p.value[2]} ${unit(metric.value)}` },
    xAxis: { type: 'category', data: cells.map((c) => fmtDay(c.day)), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: [label(metric.value)], axisLabel: { fontSize: 11 } },
    visualMap: {
      min: lo, max: hi, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
      inRange: { color: ['#e3e6dc', '#9cd67e', '#386a20'] },
      textStyle: { fontSize: 10 },
    },
    series: [{ type: 'heatmap', data, itemStyle: { borderColor: '#fbfdf6', borderWidth: 1 } }],
  }
})

const wordOption = computed(() => ({
  tooltip: { formatter: (p) => `${p.name}：${p.value}` },
  series: [{
    type: 'wordCloud', shape: 'square', left: 0, top: 0, right: 0, bottom: 0,
    sizeRange: [13, 30], rotationRange: [0, 0],
    textStyle: {
      fontFamily: 'Roboto, Noto Sans SC, sans-serif',
      color: () => `hsl(${105 + Math.random() * 40}, ${25 + Math.random() * 20}%, ${28 + Math.random() * 22}%)`,
    },
    data: words.value.slice(0, 40).map((w) => ({ name: w.word, value: w.count })),
  }],
}))
</script>

<template>
  <PageHeader
    :title="device ? device.device_id : '设备'"
    :sub="device ? `${device.edge_id} · ${device.scene || '—'} · 在线 ${device.online ? '是' : '否'}` : ''"
  >
    <RouterLink class="md-btn md-btn--text" to="/edges">返回边与设备</RouterLink>
  </PageHeader>

  <p v-if="error" class="error">{{ error }}</p>

  <template v-if="device">
    <section class="md-card">
      <div class="section-head">
        <h2>趋势</h2>
        <select v-model="metric" class="md-field" aria-label="选择指标">
          <option v-for="m in device.metrics" :key="m" :value="m">{{ label(m) }}（{{ unit(m) }}）</option>
        </select>
      </div>
      <ChartBox :option="seriesOption" height="260px" />
    </section>

    <div class="duo">
      <section class="md-card">
        <div class="section-head">
          <h2>时段热力图</h2>
          <span class="md-muted">{{ label(metric) }}（{{ unit(metric) }}）</span>
        </div>
        <ChartBox :option="heatOption" height="280px" />
      </section>

      <section class="md-card">
        <div class="section-head">
          <h2>词云</h2>
          <span class="md-muted">该边的建议用词 · 最近 7 天</span>
        </div>
        <ChartBox :option="wordOption" height="280px" />
      </section>
    </div>

    <section class="md-card advice">
      <div class="section-head">
        <h2>建议流</h2>
        <span class="md-muted">最近 {{ advice.length }} 条</span>
      </div>
      <div class="md-list">
        <div v-for="a in advice" :key="a.id" class="md-list-row">
          <span class="md-chip" :class="a.risk === 'alert' ? 'md-chip--alert' : 'md-chip--warn'">{{ a.risk }}</span>
          <div class="md-grow">
            <div>{{ a.text }}</div>
            <div class="md-muted small">{{ fmtTime(a.created_at) }} · {{ a.source }}</div>
          </div>
        </div>
        <p v-if="!advice.length" class="md-muted">这台设备最近没有建议。</p>
      </div>
    </section>
  </template>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.section-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap;
}
.duo {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4); margin-top: var(--space-4); align-items: start;
}
.advice { margin-top: var(--space-4); }
.small { font: var(--md-body-small); }
@media (max-width: 1000px) {
  .duo { grid-template-columns: minmax(0, 1fr); }
}
</style>
