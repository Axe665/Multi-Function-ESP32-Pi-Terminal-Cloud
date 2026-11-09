<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, fmtNum, fmtTime } from '../api'
import { createConstellation } from '../lib/constellation'
import StatusBar from '../components/StatusBar.vue'
import AlertList from '../components/AlertList.vue'
import Topology from '../components/Topology.vue'

const o = ref(null)
const alerts = ref([])
const latest = ref(null)
const error = ref('')
const canvas = ref(null)
let scene = null
let timer = 0

async function load() {
  try {
    const [over, al] = await Promise.all([api('/overview'), api('/alerts').catch(() => [])])
    o.value = over
    alerts.value = al
    latest.value = over.latest_ai
      ? await api(`/ai/reports/${over.latest_ai.id}`).catch(() => null)
      : null
    scene?.setData?.({
      edges: over.edges.map((e) => ({
        id: e.edge_id, devices: e.devices, online: e.online_devices > 0,
      })),
      alerts: over.alerts_total,
    })
    error.value = ''
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

const status = computed(() => o.value?.status || { level: 'ok' })

// 云端看的是 7 天口径：先给结论，再给数字
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

const measures = computed(() => {
  const v = o.value
  if (!v) return []
  const days = v.completeness_days || []
  const today = days.length ? days[days.length - 1] : 0
  const prev = days.length > 1
    ? Math.round((days.slice(0, -1).reduce((a, b) => a + b, 0) / (days.length - 1)) * 10) / 10
    : 0
  return [
    { label: '在线边节点', value: `${v.edges_online}/${v.edges_total}`, delta: null,
      note: '2 小时内有上报' },
    { label: '在线设备', value: `${v.devices_online}/${v.devices_total}`, delta: null,
      note: '与边仓口径一致' },
    { label: '7 天完整率', value: `${fmtNum(v.completeness_7d, 1)}%`, delta: null,
      note: `${fmtNum(v.rows_7d, 0)} 行日聚合；今日 ${today}%，前 6 天均 ${prev}%` },
  ]
})
</script>

<template>
  <p v-if="error" class="error">读不到总览：{{ error }}</p>

  <StatusBar :status="status" :title="headline" :sub="sub">
    <template #actions>
      <span class="md-chip">{{ o?.ai_mode === 'api' ? '公有 API' : '本地模型' }}</span>
      <RouterLink class="md-btn" to="/reports">生成 AI 报告</RouterLink>
    </template>
  </StatusBar>

  <div class="top">
    <div class="main">
      <section class="md-panel">
        <div class="section-head">
          <h2>待处理</h2>
          <span class="md-muted">{{ alerts.length }} 条</span>
        </div>
        <AlertList :alerts="alerts" :limit="4" @changed="load" />
      </section>

      <section class="md-panel">
        <div class="section-head">
          <h2>边与设备</h2>
          <span class="md-muted">每个边一行，实心格为 2 小时内上报的设备</span>
        </div>
        <Topology :edges="o?.edges || []" :cloud-name="o?.cloud_name" />
      </section>
    </div>

    <div class="side">
      <section class="md-panel">
        <div class="section-head">
          <h2>拓扑</h2>
          <span class="md-muted">连线明暗 = 上报脉冲</span>
        </div>
        <canvas ref="canvas" class="topo-canvas" aria-hidden="true"></canvas>
        <div class="topo-legend">
          <span><i class="lg lg--center"></i>本机</span>
          <span><i class="lg lg--edge"></i>在线边</span>
          <span><i class="lg lg--sat"></i>设备</span>
          <span><i class="lg lg--off"></i>离线</span>
        </div>
      </section>

      <section class="md-panel">
        <div class="section-head">
          <h2>近 7 天</h2>
          <span class="md-muted">日聚合口径</span>
        </div>
        <dl class="measures">
          <div v-for="m in measures" :key="m.label">
            <dt>{{ m.label }}</dt>
            <dd>
              <span class="md-num">{{ m.value }}</span>
              <span v-if="m.delta" class="delta" :class="m.delta > 0 ? 'up' : 'down'">
                {{ m.delta > 0 ? '+' : '' }}{{ fmtNum(m.delta, 1) }} pt
              </span>
              <span v-else-if="m.delta === 0" class="delta flat">持平</span>
              <span class="note">{{ m.note }}</span>
            </dd>
          </div>
        </dl>
      </section>

    </div>
  </div>

  <div class="bottom">
  <section class="md-section">
    <div class="section-head">
      <h2>本周报告摘要</h2>
      <span class="md-chip">{{ latest?.content?.confidence === 'high' ? '高置信' : '规则模板' }}</span>
    </div>
    <article v-if="latest" class="md-panel">
      <p class="md-muted small">{{ latest.period_start }} – {{ latest.period_end }}，{{ latest.model }}</p>
      <h3 class="headline">{{ latest.content.headline }}</h3>
      <p class="narrative">{{ latest.content.narrative }}</p>
      <ul class="actions">
        <li v-for="(a, i) in latest.content.actions || []" :key="i">{{ a }}</li>
      </ul>
      <div class="foot">
        <RouterLink class="md-btn md-btn--text" to="/reports">查看报告</RouterLink>
        <RouterLink class="md-btn md-btn--text" to="/privacy">查看审计</RouterLink>
      </div>
    </article>
    <p v-else class="md-muted">还没有报告。</p>
  </section>

  <section class="md-section">
    <div class="section-head">
      <h2>告警 Top 5</h2>
      <span class="md-muted">最近 7 天</span>
    </div>
    <div class="md-list">
      <div v-for="(a, i) in o?.alerts_top || []" :key="i" class="md-list-row">
        <div class="md-grow">
          <div>{{ a.text }}</div>
          <div class="md-muted small">{{ a.edge }}，{{ a.device }}</div>
        </div>
        <span class="sev-tag sev-tag--notice md-num">{{ a.count }}</span>
      </div>
      <p v-if="!o?.alerts_top?.length" class="md-muted">没有告警。</p>
    </div>
  </section>
  </div>
</template>

<style scoped>
.error {
  color: var(--md-error);
  margin-bottom: var(--space-3);
}
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
  flex-wrap: wrap;
}
.section-head h2 {
  font: var(--md-title-small);
  color: var(--md-on-surface);
}

.top {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 1fr);
  gap: var(--space-4);
  margin-top: var(--space-4);
  align-items: start;
}
.main {
  display: grid;
  gap: var(--space-4);
}
.side {
  display: grid;
  gap: var(--space-4);
}

.measures {
  display: grid;
  gap: var(--space-2);
}
.measures div {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: baseline;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--md-card-border);
}
.measures div:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.measures dt {
  font: var(--md-body-small);
  color: var(--md-on-surface-variant);
}
.measures dd {
  text-align: right;
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  justify-content: flex-end;
  flex-wrap: wrap;
}
.measures .md-num {
  font: var(--md-title-medium);
  color: var(--md-on-surface);
}
.delta {
  font: var(--md-label-medium);
}
.delta.up { color: var(--md-ok); }
.delta.down { color: var(--md-notice); }
.delta.flat { color: var(--md-on-surface-variant); }
.note {
  flex-basis: 100%;
  font: var(--md-body-small);
  color: var(--md-on-surface-variant);
}

.topo-canvas {
  width: 100%;
  height: 128px;
  display: block;
  border-radius: var(--md-shape-sm);
}
.topo-legend {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-top: var(--space-2);
  font: var(--md-label-small);
  color: var(--md-on-surface-variant);
}
.topo-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.lg {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.lg--center { background: #1a1f18; }
.lg--edge { background: #0f6b38; }
.lg--sat { background: #858c85; }
.lg--off { background: #b9c0b9; }
[data-theme='dark'] .lg--center { background: #ebf5e4; }
[data-theme='dark'] .lg--edge { background: #85db9e; }
[data-theme='dark'] .lg--sat { background: #9eada1; }
[data-theme='dark'] .lg--off { background: #4a514c; }

.bottom {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: var(--space-4);
  margin-top: var(--space-4);
  align-items: start;
}
.bottom .md-section {
  margin-top: 0;
}

.headline {
  font: var(--md-title-large);
  margin: var(--space-2) 0;
}
.narrative {
  font: var(--md-body-medium);
  color: var(--md-on-surface);
  max-width: 92ch;
}
.actions {
  margin: var(--space-3) 0;
  padding-left: 18px;
  font: var(--md-body-medium);
  color: var(--md-on-surface-variant);
}
.foot {
  display: flex;
  gap: var(--space-2);
}
.small {
  font: var(--md-body-small);
}

@media (max-width: 1100px) {
  .top,
  .bottom {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
