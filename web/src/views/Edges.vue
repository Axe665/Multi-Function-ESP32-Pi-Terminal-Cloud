<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import PageLead from '../components/PageLead.vue'
import Icon from '../components/Icon.vue'
import ChartBox from '../components/ChartBox.vue'
import { chartTokens, useTheme } from '../lib/theme'

const edges = ref([])
const devices = ref([])
const activeEdge = ref('')
const error = ref('')
const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))

// 每边在线设备与有数据天数，一眼看出谁掉队
const compareOption = computed(() => {
  const t = tk.value
  return {
    grid: { left: 4, right: 8, top: 28, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0, itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11, color: t.axis } },
    xAxis: { type: 'category', data: edges.value.map((e) => e.name),
             axisLabel: { fontSize: 11, color: t.axis }, axisLine: { lineStyle: { color: t.split } }, axisTick: { show: false } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 10, color: t.axis } },
    series: [
      { name: '在线设备', type: 'bar', barMaxWidth: 26,
        data: edges.value.map((e) => e.online_devices), itemStyle: { color: t.line, borderRadius: [3, 3, 0, 0] } },
      { name: '设备总数', type: 'bar', barMaxWidth: 26,
        data: edges.value.map((e) => e.devices), itemStyle: { color: t.split, borderRadius: [3, 3, 0, 0] } },
    ],
  }
})

// 文档 §6.4 的两级下钻：先点边，再看这台边下面的设备
const shown = computed(() =>
  activeEdge.value ? devices.value.filter((d) => d.edge_id === activeEdge.value) : devices.value,
)

onMounted(async () => {
  try {
    ;[edges.value, devices.value] = await Promise.all([api('/edges'), api('/devices')])
  } catch (e) {
    error.value = e.message
  }
})

const nameOf = (id) => edges.value.find((e) => e.edge_id === id)?.name || id
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <PageLead :sub="`${edges.length} 个边，${devices.length} 台设备。选择边可筛选设备，选择设备查看趋势。`">
    <button v-if="activeEdge" class="md-btn md-btn--text" @click="activeEdge = ''">显示全部边</button>
  </PageLead>

  <div class="md-grid">
    <section class="md-card span-12">
      <div class="md-card-head">
        <h2>边对比</h2>
        <span class="meta">在线设备 / 设备总数，最近 7 天</span>
      </div>
      <ChartBox :option="compareOption" height="150px" label="各边在线设备与设备总数" />
    </section>

    <section class="md-card span-12">
      <div class="md-card-head">
        <h2>边节点</h2>
        <span class="meta">最近 7 天，点一行筛选设备</span>
      </div>
      <div class="md-list">
        <button
          v-for="e in edges"
          :key="e.edge_id"
          type="button"
          class="md-list-item edge-row"
          :class="{ 'edge-row--active': activeEdge === e.edge_id }"
          :aria-pressed="activeEdge === e.edge_id"
          @click="activeEdge = activeEdge === e.edge_id ? '' : e.edge_id"
        >
          <span class="md-list-icon"><Icon name="hub" /></span>
          <div class="md-grow">
            <div class="md-list-title">{{ e.name }}</div>
            <div class="md-list-sub md-mono">{{ e.edge_id }}</div>
          </div>
          <div class="md-list-trail">
            <span class="md-chip" :class="e.group_analysis === false ? 'md-chip--idle' : 'md-chip--ok'">
              {{ e.group_analysis === false ? '仅计数' : '群体分析' }}
            </span>
            <span class="stat"><b>{{ e.online_devices }}</b><span>/ {{ e.devices }} 在线</span></span>
            <span class="stat"><b>{{ e.days }}</b><span>/ 7 天有数据</span></span>
            <span class="stat"><b>{{ e.k_min }}</b><span>最小 k</span></span>
            <span class="stat"><b>{{ e.metrics.length }}</b><span>指标</span></span>
            <Icon name="chevron_right" />
          </div>
        </button>
      </div>
    </section>

    <section class="md-card span-12">
      <div class="md-card-head">
        <h2>设备</h2>
        <span class="meta">{{ activeEdge ? nameOf(activeEdge) + '，' : '' }}{{ shown.length }} 台</span>
      </div>
      <div class="md-list">
        <RouterLink v-for="d in shown" :key="d.device_id" :to="`/devices/${d.device_id}`" class="md-list-item">
          <span class="md-list-icon"><Icon name="devices" /></span>
          <div class="md-grow">
            <div class="md-list-title md-mono">{{ d.device_id }}</div>
            <div class="md-list-sub">
              {{ nameOf(d.edge_id) }}<span v-if="d.group_analysis === false">（仅计数）</span> · {{ d.scene || '—' }}
            </div>
          </div>
          <div class="md-list-trail">
            <span class="stat"><b>{{ d.days }}</b><span>/ 7 天有数据</span></span>
            <span class="stat"><b>{{ d.k_min }}</b><span>最小 k</span></span>
            <span class="sev" :class="d.online ? 'sev--ok' : 'sev--critical'">
              <span class="sev-dot"></span>{{ d.online ? '在线' : '离线' }}
            </span>
            <Icon name="chevron_right" />
          </div>
        </RouterLink>
        <p v-if="!shown.length" class="md-muted">这个边还没有设备上报。</p>
      </div>
      <p class="md-muted small hint">最近一次上报 {{ fmtTime(Math.max(...devices.map((d) => d.last_seen || 0))) }}。</p>
    </section>
  </div>
</template>

<style scoped>
.error { color: var(--md-error); max-width: var(--content-max); margin: 0 auto var(--space-3); }
.edge-row {
  width: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.edge-row--active {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
}
.stat { display: flex; align-items: baseline; gap: 4px; color: var(--md-on-surface-variant); font: var(--md-label-medium); }
.stat b { font: var(--md-title-small); color: var(--md-on-surface); }
.small { font: var(--md-body-small); }
.hint { margin-top: var(--space-3); }
</style>
