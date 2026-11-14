<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, fmtNum, fmtDay } from '../api'
import PageLead from '../components/PageLead.vue'
import ChartBox from '../components/ChartBox.vue'
import { chartTokens, useTheme } from '../lib/theme'

const meta = ref({ metrics: [] })
const metric = ref('')
const scope = ref('edges')
const data = ref(null)
const error = ref('')
const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))

async function loadMeta() {
  meta.value = await api('/meta')
  metric.value = meta.value.metrics[0]?.key || ''
}

async function loadCompare() {
  if (!metric.value) return
  data.value = await api(`/compare?metric=${encodeURIComponent(metric.value)}&scope=${scope.value}`)
}

onMounted(async () => {
  try {
    await loadMeta()
    await loadCompare()
  } catch (e) {
    error.value = e.message
  }
})
watch([metric, scope], loadCompare)

const series = computed(() => data.value?.edges || data.value?.devices || [])

const option = computed(() => {
  const edges = series.value
  return {
    color: tk.value.palette,
    grid: { left: 8, right: 12, top: 30, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10,
              textStyle: { fontSize: 11, color: tk.value.axis } },
    xAxis: {
      type: 'category',
      data: data.value?.days || [],
      axisLabel: { formatter: (v) => fmtDay(v), fontSize: 11, color: tk.value.axis },
      axisLine: { lineStyle: { color: tk.value.split } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: tk.value.split } },
      axisLabel: { fontSize: 11, color: tk.value.axis, formatter: '{value}' },
    },
    series: edges.map((e, i) => ({
      name: e.name,
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 2 },
      data: (data.value?.days || []).map((d) => e.points.find((p) => p.day === d)?.index ?? null),
      // 首日 100 是这页唯一的比较基准，画出来省得用户自己找
      ...(i === 0 ? {
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: tk.value.axis, type: 'dashed', width: 1 },
          label: { formatter: '首日 = 100', color: tk.value.axis, fontSize: 10, position: 'insideEndTop' },
          data: [{ yAxis: 100 }],
        },
      } : {}),
    })),
  }
})

const rows = computed(() => {
  return series.value.map((e) => {
    const last = e.points[e.points.length - 1]
    const first = e.points[0]
    return { ...e, last, first }
  })
})
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <PageLead :sub="`同一指标在${scope === 'edges' ? '不同边' : '不同设备'}之间的走势，首日统一为 100，只比变化不比绝对值。`">
    <label class="md-field pick">
      <span>指标</span>
      <select v-model="metric">
        <option v-for="m in meta.metrics" :key="m.key" :value="m.key">{{ m.label }}（{{ m.unit }}）</option>
      </select>
    </label>
    <label class="md-field pick">
      <span>对比范围</span>
      <select v-model="scope">
        <option value="edges">按边</option>
        <option value="devices">按设备</option>
      </select>
    </label>
  </PageLead>

  <div class="md-grid">
    <section class="md-card span-12">
      <div class="md-card-head">
        <h2>{{ scope === 'edges' ? '跨边走势' : '跨设备走势' }}</h2>
        <span class="meta">{{ data?.metric }}，{{ (data?.days || []).length }} 天</span>
      </div>
      <ChartBox :option="option" height="320px" label="归一化后的走势对比" />
    </section>

    <section class="md-card span-12">
      <div class="md-card-head">
        <h2>末日均值</h2>
        <span class="meta">变化按首日归一到 100 折算</span>
      </div>
      <table class="md-table">
        <thead>
          <tr><th>{{ scope === 'edges' ? '边' : '设备' }}</th><th class="num">首日均值</th><th class="num">末日均值</th><th class="num">较首日</th><th class="num">最小 k</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.device_id || r.edge_id">
            <td>{{ r.name }}<div class="md-mono md-muted">{{ r.edge_id }}</div></td>
            <td class="num">{{ fmtNum(r.first?.value, 2) }}</td>
            <td class="num">{{ fmtNum(r.last?.value, 2) }}</td>
            <td class="num">
              <span class="change">
                {{ (r.last?.index ?? 100) >= 100 ? '+' : '−' }}{{ fmtNum(Math.abs((r.last?.index ?? 100) - 100), 1) }}%
              </span>
            </td>
            <td class="num">{{ r.k_min }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
/* 涨跌本身不是告警，颜色留给阈值突破 */
.change { font-variant-numeric: tabular-nums; }
.error { color: var(--md-error); max-width: var(--content-max); margin: 0 auto var(--space-3); }
.pick { width: 220px; }
</style>
