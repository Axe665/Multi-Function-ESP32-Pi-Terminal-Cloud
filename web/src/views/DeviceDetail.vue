<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, fmtDay, fmtTime } from '../api'
import PageHeader from '../components/PageHeader.vue'
import ChartBox from '../components/ChartBox.vue'
import { chartTokens, useTheme } from '../lib/theme'
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
const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))

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
      axisLine: { lineStyle: { color: tk.value.split } },
      axisLabel: { fontSize: 11, color: tk.value.axis },
    },
    yAxis: {
      type: 'value', scale: true,
      splitLine: { lineStyle: { color: tk.value.split } },
      axisLabel: { fontSize: 11, color: tk.value.axis },
    },
    series: [{
      type: 'line', smooth: true, symbol: 'none',
      lineStyle: { width: 2, color: tk.value.line },
      areaStyle: { color: tk.value.area },
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
    xAxis: { type: 'category', data: cells.map((c) => fmtDay(c.day)),
             axisLine: { lineStyle: { color: tk.value.split } },
             axisLabel: { fontSize: 11, color: tk.value.axis } },
    yAxis: { type: 'category', data: [label(metric.value)],
             axisLabel: { fontSize: 11, color: tk.value.axis } },
    visualMap: {
      min: lo, max: hi, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
      inRange: { color: tk.value.heat },
      textStyle: { fontSize: 10, color: tk.value.axis },
    },
    series: [{ type: 'heatmap', data, itemStyle: { borderColor: tk.value.border, borderWidth: 1 } }],
  }
})

const wordOption = computed(() => {
  const word = tk.value.word
  return {
    tooltip: { formatter: (p) => `${p.name}：${p.value}` },
    series: [{
      type: 'wordCloud', shape: 'square', left: 0, top: 0, right: 0, bottom: 0,
      sizeRange: [15, 38], rotationRange: [0, 0],
      textStyle: {
        fontFamily: 'Roboto, Noto Sans SC, sans-serif',
        fontWeight: 500,
        color: () => {
          const [s0, s1] = word.sat
          const [l0, l1] = word.light
          return `hsl(${112 + Math.random() * 34}, ${s0 + Math.random() * (s1 - s0)}%, ${l0 + Math.random() * (l1 - l0)}%)`
        },
      },
      data: words.value.slice(0, 40).map((w) => ({ name: w.word, value: w.count })),
    }],
  }
})
</script>

<template>
  <PageHeader
    :title="device ? device.device_id : '设备'"
    :sub="device ? `${device.edge_id}，${device.scene || '—'}，在线：${device.online ? '是' : '否'}` : ''"
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

    <section class="md-card strip">
      <div class="section-head">
        <h2>按日聚合</h2>
        <span class="md-muted">{{ label(metric) }}，色深为当日均值（云端没有小时粒度）</span>
      </div>
      <ChartBox :option="heatOption" height="140px" />
    </section>

    <div class="duo">
      <section class="md-card">
        <div class="section-head">
          <h2>词云</h2>
          <span class="md-muted">该边最近 7 天的建议用词</span>
        </div>
        <ChartBox :option="wordOption" height="280px" />
      </section>

      <section class="md-card advice">
      <div class="section-head">
        <h2>建议</h2>
        <span class="md-muted">最近 {{ advice.length }} 条</span>
      </div>
      <div class="md-list">
        <div v-for="a in advice" :key="a.id" class="md-list-row">
          <span class="sev-tag" :class="a.risk === 'alert' ? 'sev-tag--notice' : 'sev-tag--idle'">
            {{ a.risk === 'alert' ? '告警' : '关注' }}
          </span>
          <div class="md-grow">
            <div>{{ a.text }}</div>
            <div class="md-muted small">{{ fmtTime(a.created_at) }}，{{ a.source }}</div>
          </div>
        </div>
        <p v-if="!advice.length" class="md-muted">这台设备最近没有建议。</p>
      </div>
      </section>
    </div>
  </template>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.section-head {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap;
}
.duo > .md-card { display: flex; flex-direction: column; }
.duo .md-list { flex: 1; justify-content: space-around; }
.duo {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4); margin-top: var(--space-4); align-items: stretch;
}
.advice { margin-top: 0; }
.strip { margin-top: var(--space-4); }
.small { font: var(--md-body-small); }
@media (max-width: 1000px) {
  .duo { grid-template-columns: minmax(0, 1fr); }
}
</style>
