<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtNum, fmtTime } from '../api'
import { chartTokens, useTheme } from '../lib/theme'
import StatusBar from '../components/StatusBar.vue'
import AlertList from '../components/AlertList.vue'
import ChartBox from '../components/ChartBox.vue'
import Topology from '../components/Topology.vue'

const o = ref(null)
const alerts = ref([])
const devices = ref([])
const counts = ref([])
const latest = ref(null)
const error = ref('')
let timer = 0
const router = useRouter()
const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))

async function load() {
  try {
    const [over, al, dev, cnt] = await Promise.all([
      api('/overview'),
      api('/alerts').catch(() => []),
      api('/devices').catch(() => []),
      api('/daily/counts?days=7').catch(() => []),
    ])
    o.value = over
    alerts.value = al
    devices.value = dev
    counts.value = cnt
    latest.value = over.latest_ai
      ? await api(`/ai/reports/${over.latest_ai.id}`).catch(() => null)
      : null
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  await load()
  timer = setInterval(load, 20000)
})
onBeforeUnmount(() => clearInterval(timer))

const status = computed(() => o.value?.status || { level: 'ok' })

const headline = computed(() => {
  const v = o.value
  if (!v) return '总览'
  const s = v.status
  if (s.level === 'critical') {
    return s.stale_edges && s.stale_edges === s.critical
      ? `${s.stale_edges} 个边掉线`
      : `${s.critical} 条严重异常待处理`
  }
  if (s.level === 'notice') return `${s.notice} 条异常待确认`
  const handled = (s.acked || 0) + (s.muted || 0)
  return `${v.edges_online}/${v.edges_total} 个边在线，${handled ? `${handled} 条已处理` : '没有待处理异常'}`
})

const sub = computed(() => {
  const v = o.value
  if (!v) return ''
  return `${v.edges_total} 个边、${v.devices_total} 台设备，只接收 k≥5 的日聚合；更新于 ${fmtTime(v.generated_at, true)}`
})

const dayLabel = (day, list) => {
  const i = list.indexOf(day)
  return i === list.length - 1 ? '今天' : `D-${list.length - 1 - i}`
}

// 1. 近 7 天完整率
const completeOption = computed(() => {
  const t = tk.value
  const days = o.value?.completeness_days || []
  const labels = days.map((_, i) => (i === days.length - 1 ? '今天' : `D-${days.length - 1 - i}`))
  return {
    grid: { left: 4, right: 8, top: 24, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}%` },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 12, color: t.axis },
             axisLine: { lineStyle: { color: t.split } }, axisTick: { show: false } },
    yAxis: { type: 'value', max: 100, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 12, color: t.axis, formatter: '{value}%' } },
    series: [{
      type: 'bar', data: days, barWidth: '58%',
      itemStyle: {
        color: (p) => (p.value < 80 ? '#c98a2a' : t.line),
        borderRadius: [3, 3, 0, 0],
      },
      label: {
        show: true, position: 'top', fontSize: 12, color: t.axis,
        formatter: (p) => (p.value < 100 ? `${p.value}%` : ''),
      },
    }],
  }
})

// 2. 各边每日入库行数（堆叠）
const rowsOption = computed(() => {
  const t = tk.value
  const edges = o.value?.edges || []
  const days = [...new Set(counts.value.map((r) => r.day))].sort()
  const series = edges.map((e, i) => ({
    name: e.name || e.edge_id,
    type: 'bar',
    stack: 'rows',
    barWidth: '60%',
    itemStyle: { color: t.palette[i % t.palette.length] },
    data: days.map((d) => {
      const hit = counts.value.find((r) => r.day === d && r.edge_id === e.edge_id)
      return hit ? hit.n : 0
    }),
  }))
  return {
    grid: { left: 4, right: 8, top: 28, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 12, color: t.axis } },
    xAxis: { type: 'category', data: days.map((d) => dayLabel(d, days)),
             axisLabel: { fontSize: 12, color: t.axis },
             axisLine: { lineStyle: { color: t.split } }, axisTick: { show: false } },
    yAxis: { type: 'value', splitNumber: 3, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 12, color: t.axis } },
    series,
  }
})

// 3. 指标覆盖：每台设备在报几个指标
const coverOption = computed(() => {
  const t = tk.value
  const rows = (devices.value || []).slice(0, 12)
  return {
    grid: { left: 4, right: 8, top: 16, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const d = rows[p[0].dataIndex]
        return `${d.device_id}<br/>${(d.metrics || []).length} 个指标，最小 k ${d.k_min ?? '—'}`
      },
    },
    xAxis: {
      type: 'category', data: rows.map((d) => d.device_id),
      axisLabel: { fontSize: 12, color: t.axis, rotate: 40, interval: 0 },
      axisLine: { lineStyle: { color: t.split } }, axisTick: { show: false },
    },
    yAxis: { type: 'value', minInterval: 1, interval: 2, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 12, color: t.axis } },
    series: [{
      type: 'bar', barWidth: '55%',
      data: rows.map((d) => ({ value: (d.metrics || []).length,
                               itemStyle: { color: d.online ? t.line : t.axis, borderRadius: [3, 3, 0, 0] } })),
      label: { show: false },
    }],
  }
})

// 4. 告警 Top 5（横条）
const alertsOption = computed(() => {
  const t = tk.value
  const top = (o.value?.alerts_top || []).slice(0, 5).reverse()
  const max = Math.max(1, ...top.map((a) => a.count))
  const step = Math.max(1, Math.ceil(max / 3))
  return {
    grid: { left: 4, right: 26, top: 6, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const a = top[p[0].dataIndex]
        return `${a.text}<br/>${a.edge}，${a.device}，${a.count} 次`
      },
    },
    xAxis: { type: 'value', max: step * 3, interval: step, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 12, color: t.axis } },
    yAxis: {
      type: 'category',
      data: top.map((a) => (a.text.length > 14 ? a.text.slice(0, 14) + '…' : a.text)),
      axisLabel: { fontSize: 12, color: t.text, width: 110, overflow: 'truncate' },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{
      type: 'bar', data: top.map((a) => a.count), barWidth: '55%',
      itemStyle: { color: '#c98a2a', borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', fontSize: 12, color: t.axis },
    }],
  }
})

// 5. 边在线率
const edgeOption = computed(() => {
  const t = tk.value
  const edges = o.value?.edges || []
  return {
    grid: { left: 4, right: 34, top: 6, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const e = edges[p[0].dataIndex]
        return `${e.name}<br/>${e.online_devices} / ${e.devices} 台在线`
      },
    },
    xAxis: { type: 'value', max: 100, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 12, color: t.axis, formatter: '{value}%' } },
    yAxis: { type: 'category', data: edges.map((e) => e.name),
             axisLabel: { fontSize: 13, color: t.text }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      type: 'bar', barWidth: '55%',
      data: edges.map((e) => (e.devices ? Math.round((e.online_devices / e.devices) * 100) : 0)),
      itemStyle: {
        color: (p) => (p.value === 0 ? t.axis : t.line),
        borderRadius: [0, 3, 3, 0],
      },
      label: {
        show: true, position: 'right', fontSize: 12, color: t.axis,
        formatter: (p) => `${edges[p.dataIndex].online_devices}/${edges[p.dataIndex].devices}`,
      },
    }],
  }
})

function pickTopo(kind, d) {
  if (kind === 'device') router.push(`/devices/${d.device_id}`)
  else if (kind === 'edge') router.push('/edges')
}

function drillCover(p) {
  const d = devices.value[p.dataIndex]
  if (d) router.push(`/devices/${d.device_id}`)
}
</script>

<template>
  <p v-if="error" class="error">读不到总览：{{ error }}</p>

  <StatusBar :status="status" :title="headline" :sub="sub">
    <template #actions>
      <span class="md-chip">{{ o?.ai_mode === 'api' ? '公有 API' : '本地模型' }}</span>
      <RouterLink class="md-btn" to="/reports">生成 AI 报告</RouterLink>
    </template>
  </StatusBar>

  <div class="board">
    <section class="md-panel wide">
      <div class="section-head">
        <h2>全网拓扑</h2>
        <span class="md-muted">边名常显，设备悬停看名字；滚轮缩放</span>
      </div>
      <div class="chart"><Topology :edges="o?.edges || []" :devices="devices" :cloud-name="o?.cloud_name" @pick="pickTopo" /></div>
    </section>

    <aside class="side">
      <section class="md-panel">
        <div class="section-head">
          <h2>待处理</h2>
          <span class="md-muted">{{ o ? alerts.length : '—' }} 条</span>
        </div>
        <AlertList :alerts="alerts" :limit="4" @changed="load" />
      </section>

    <section class="md-section">
      <div class="section-head">
        <h2>边在线率</h2>
        <span class="md-muted">2 小时内上报</span>
      </div>
      <div class="chart"><ChartBox :option="edgeOption" height="100%" @click="router.push('/edges')" /></div>
    </section>

      <section class="md-section">
        <div class="section-head">
          <h2>本周报告摘要</h2>
          <span class="md-chip">{{ latest?.content?.confidence === 'high' ? '高置信' : '规则模板' }}</span>
        </div>
        <article v-if="latest" class="md-panel brief">
          <h3>{{ latest.content.headline }}</h3>
          <p class="md-muted small">{{ latest.period_start }} – {{ latest.period_end }}，{{ latest.model }}</p>
          <p class="narrative">{{ latest.content.narrative }}</p>
          <RouterLink class="md-btn md-btn--text" to="/reports">查看报告</RouterLink>
        </article>
        <p v-else class="md-muted">还没有报告。</p>
      </section>

    </aside>

    <section class="md-panel">
      <div class="section-head">
        <h2>近 7 天完整率</h2>
        <span class="md-muted">合计 {{ fmtNum(o?.completeness_7d, 1) }}%</span>
      </div>
      <div class="chart"><ChartBox :option="completeOption" height="100%" /></div>
    </section>

    <section class="md-panel">
      <div class="section-head">
        <h2>各边每日入库</h2>
        <span class="md-muted">行日聚合</span>
      </div>
      <div class="chart"><ChartBox :option="rowsOption" height="100%" @click="router.push('/edges')" /></div>
    </section>

    <section class="md-panel">
      <div class="section-head">
        <h2>指标覆盖</h2>
        <span class="md-muted">点柱子进设备详情</span>
      </div>
      <div class="chart"><ChartBox :option="coverOption" height="100%" :on-click="drillCover" /></div>
    </section>

      <section class="md-panel">
        <div class="section-head">
        <h2>告警 Top 5</h2>
        <span class="md-muted">最近 7 天，点条看审计</span>
        </div>
      <ChartBox :option="alertsOption" height="145px" @click="router.push('/privacy')" />
    </section>



  </div>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }

.board {
  flex: 1;
  min-height: 600px;
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr) clamp(340px, 21vw, 430px);
  grid-template-rows: minmax(0, 1.25fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: clamp(10px, 0.9vw, 16px);
  margin-top: clamp(10px, 1vw, 16px);
  align-items: stretch;
}
.wide { grid-column: span 2; }
.board > section { display: flex; flex-direction: column; min-height: 0; }
.board .chart { flex: 1; min-height: 0; }
.side {
  grid-column: 3;
  grid-row: 1 / span 3;
  display: flex;
  flex-direction: column;
  gap: clamp(10px, 0.9vw, 16px);
  min-width: 0;
  min-height: 0;
}
.side > section { display: flex; flex-direction: column; min-height: 0; }
.side .brief { overflow: hidden; }

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  flex-wrap: wrap;
}
.section-head h2 { font: var(--md-title-small); }

.brief h3 { font: var(--md-title-medium); margin-bottom: 2px; }
.narrative {
  font: var(--md-body-small);
  color: var(--md-on-surface-variant);
  margin: var(--space-2) 0;
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.small { font: var(--md-body-small); }

@media (max-width: 1180px) {
  .board { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .side { grid-column: 1 / -1; grid-row: auto; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .board { grid-template-columns: minmax(0, 1fr); }
  .wide { grid-column: auto; }
  .side { grid-template-columns: minmax(0, 1fr); }
}
</style>
