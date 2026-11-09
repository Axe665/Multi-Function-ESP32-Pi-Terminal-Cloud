<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, fmtNum, fmtTime } from '../api'
import { createConstellation } from '../lib/constellation'
import PageHeader from '../components/PageHeader.vue'
import KpiRow from '../components/KpiRow.vue'

const o = ref(null)
const latest = ref(null)
const error = ref('')
const canvas = ref(null)
let scene = null
let timer = 0

async function load() {
  try {
    o.value = await api('/overview')
    latest.value = o.value.latest_ai ? await api(`/ai/reports/${o.value.latest_ai.id}`).catch(() => null) : null
    scene?.setData?.({
      edges: o.value.edges.map((e) => ({ id: e.edge_id, devices: e.devices, online: e.online_devices > 0 })),
      alerts: o.value.alerts_total,
    })
  } catch (e) {
    error.value = e.message
  }
}

onMounted(async () => {
  if (canvas.value) scene = createConstellation(canvas.value)
  await load()
  timer = setInterval(load, 20000)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  scene?.destroy?.()
})

const kpis = computed(() => {
  const v = o.value
  if (!v) return []
  return [
    { label: '在线边节点', value: `${v.edges_online}/${v.edges_total}`, note: '2 小时内有上报' },
    { label: '在线设备', value: `${v.devices_online}/${v.devices_total}`, note: '与边口径一致' },
    { label: '7 天完整率', value: `${fmtNum(v.completeness_7d, 1)}%`, note: `${fmtNum(v.rows_7d, 0)} 行日聚合` },
    { label: '待处理告警', value: fmtNum(v.alerts_total, 0), note: `最小 k 见 ${v.k_min_seen}` },
  ]
})
</script>

<template>
  <PageHeader
    title="总览"
    :sub="`${o?.edges_total || 0} 个边 · ${o?.devices_total || 0} 台设备 · 最近 7 天上报完整率 ${fmtNum(o?.completeness_7d, 1)}% · 只接收 k≥5 的日聚合`"
  >
    <span class="md-chip">{{ o?.ai_mode === 'api' ? '公有 API' : '本地模型' }}</span>
    <RouterLink class="md-btn" to="/reports">生成 AI 报告</RouterLink>
  </PageHeader>

  <p v-if="error" class="error">{{ error }}</p>
  <KpiRow :items="kpis" />

  <section class="md-card">
    <div class="section-head">
      <h2>全网拓扑</h2>
      <span class="md-muted">本机在中心 · 每条连线是一份 edge-report 的来路</span>
    </div>
    <canvas ref="canvas" class="topology"></canvas>
    <div class="topo-list">
      <div v-for="e in o?.edges || []" :key="e.edge_id" class="md-list-row">
        <span class="dot" :class="e.online_devices ? 'dot--on' : ''"></span>
        <div class="md-grow">
          <div>{{ e.name }}</div>
          <div class="md-mono md-muted">{{ e.edge_id }}</div>
        </div>
        <span class="md-mono">{{ e.online_devices }} / {{ e.devices }}</span>
      </div>
    </div>
  </section>

  <div class="trio">
    <section class="md-card">
      <div class="section-head">
        <h2>本周 AI 摘要</h2>
        <span class="md-chip">{{ latest?.content?.confidence === 'high' ? '高置信' : '规则模板' }}</span>
      </div>
      <template v-if="latest">
        <p class="md-muted small">{{ latest.period_start }} – {{ latest.period_end }} · {{ latest.model }}</p>
        <h3 class="headline">{{ latest.content.headline }}</h3>
        <p class="narrative">{{ latest.content.narrative }}</p>
        <ul class="actions">
          <li v-for="(a, i) in latest.content.actions || []" :key="i">{{ a }}</li>
        </ul>
        <RouterLink class="md-btn md-btn--text" to="/reports">打开报告</RouterLink>
      </template>
      <p v-else class="md-muted">还没有报告。</p>
    </section>

    <section class="md-card">
      <div class="section-head">
        <h2>告警 Top 5</h2>
        <span class="md-muted">最近 7 天</span>
      </div>
      <div class="md-list">
        <div v-for="(a, i) in o?.alerts_top || []" :key="i" class="md-list-row">
          <div class="md-grow">
            <div>{{ a.text }}</div>
            <div class="md-muted small">{{ a.edge }} · {{ a.device }}</div>
          </div>
          <span class="md-chip" :class="a.risk === 'alert' ? 'md-chip--alert' : 'md-chip--warn'">{{ a.count }}</span>
        </div>
        <p v-if="!o?.alerts_top?.length" class="md-muted">没有告警。</p>
      </div>
    </section>

    <section class="md-card">
      <div class="section-head">
        <h2>隐私</h2>
        <RouterLink class="md-btn md-btn--text" to="/privacy">看审计</RouterLink>
      </div>
      <div class="md-list">
        <div class="md-list-row"><div class="md-grow">被抑制的桶</div><span class="md-mono">{{ fmtNum(o?.suppressed_buckets, 0) }}</span></div>
        <div class="md-list-row"><div class="md-grow">原始字段拦截</div><span class="md-mono">{{ o?.raw_rejected ?? 0 }}</span></div>
        <div class="md-list-row"><div class="md-grow">最小 k</div><span class="md-mono">{{ o?.k_min_seen ?? '—' }}</span></div>
      </div>
      <p class="md-muted small">只存日聚合与建议文本。更新于 {{ fmtTime(o?.generated_at, true) }}。</p>
    </section>
  </div>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.section-head {
  display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-3);
  margin-bottom: var(--space-3); flex-wrap: wrap;
}
.topo-list {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 var(--space-4); margin-top: var(--space-2);
}
.topology {
  width: 100%; height: 300px; display: block;
  border-radius: var(--md-shape-sm);
  /* 星座图是加色混合，配反色底 */
  background: var(--md-inverse-surface);
}
.trio {
  display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr) minmax(0, 0.9fr);
  gap: var(--space-4); margin-top: var(--space-4); align-items: start;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--md-outline); }
.dot--on { background: var(--md-primary); }
.headline { font: var(--md-title-large); margin: var(--space-2) 0; }
.narrative { font: var(--md-body-medium); color: var(--md-on-surface); }
.actions { margin: var(--space-3) 0; padding-left: 18px; font: var(--md-body-medium); color: var(--md-on-surface-variant); }
.small { font: var(--md-body-small); }
@media (max-width: 1100px) {
  .topo-grid, .trio { grid-template-columns: minmax(0, 1fr); }
}
</style>
