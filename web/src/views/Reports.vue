<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtDay, fmtNum, fmtTime } from '../api'
import PageLead from '../components/PageLead.vue'
import ChartBox from '../components/ChartBox.vue'
import { chartTokens, useTheme } from '../lib/theme'
import { useSnack } from '../components/useSnack'

const list = ref([])
const detail = ref(null)
const active = ref(null)
const health = ref(null)
const scope = ref('fleet')
const task = ref('weekly_narrative')
const busy = ref(false)
const error = ref('')
const { toast } = useSnack()

const scopeLabel = { fleet: '全网', edge: '单边', device: '单设备' }
const taskLabel = {
  weekly_narrative: '趋势解读',
  fleet_compare: '跨边对比',
  advice_digest: '本周三件事',
  wordcloud_label: '词云主题',
}

async function load() {
  try {
    list.value = await api('/ai/reports?limit=30')
    health.value = await api('/health').catch(() => null)
    const id = active.value || list.value[0]?.id
    if (id) await open(id)
  } catch (e) {
    error.value = e.message
  }
}

async function open(id) {
  active.value = id
  detail.value = await api(`/ai/reports/${id}`).catch(() => null)
}

async function regenerate() {
  if (!detail.value) return
  busy.value = true
  try {
    await api('/ai/reports', {
      method: 'POST',
      body: {
        scope: detail.value.scope,
        scope_id: detail.value.scope_id || undefined,
        task: detail.value.task || 'weekly_narrative',
        period: { from: detail.value.period_start, to: detail.value.period_end },
      },
    })
    toast('已按原区间重新生成')
    await load()
  } catch (e) {
    toast(`生成失败：${e.message}`)
  } finally {
    busy.value = false
  }
}

async function generate() {
  busy.value = true
  try {
    const body = { scope: scope.value, task: task.value }
    if (scope.value === 'edge') body.scope_id = list.value.find((r) => r.scope_id)?.scope_id || 'edge-lab-01'
    const out = await api('/ai/reports', { method: 'POST', body })
    if (out.queued) toast(`已排队：${out.job_id}`)
    else toast(`报告 #${out.id} 已生成`)
    await load()
  } catch (e) {
    toast(`生成失败：${e.message}`)
  } finally {
    busy.value = false
  }
}

onMounted(load)

const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))
const taskOption = computed(() => {
  const t = tk.value
  const rows = byTask.value.slice().reverse()
  return {
    grid: { left: 4, right: 26, top: 6, bottom: 4, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: t.split } },
             axisLabel: { fontSize: 10, color: t.axis } },
    yAxis: { type: 'category', data: rows.map((r) => r.label),
             axisLabel: { fontSize: 11, color: t.text }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      type: 'bar', data: rows.map((r) => r.n), barMaxWidth: 16,
      itemStyle: { color: t.line, borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', fontSize: 10, color: t.axis },
    }],
  }
})

const byTask = computed(() =>
  Object.keys(taskLabel).map((k) => ({
    key: k,
    label: taskLabel[k],
    n: list.value.filter((r) => (r.task || 'weekly_narrative') === k).length,
  })),
)
const cacheHits = computed(() => {
  const hashes = list.value.map((r) => r.prompt_hash).filter(Boolean)
  return hashes.length - new Set(hashes).size
})
const used = computed(() => health.value?.api_tokens_used ?? 0)
const budget = computed(() => health.value?.api_budget ?? 0)
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>

  <PageLead sub="默认使用本地 Ollama。模型不可用时落规则模板，置信度标为低。">
    <label class="md-field pick">
      <span>范围</span>
      <select v-model="scope">
        <option value="fleet">全网</option>
        <option value="edge">单边</option>
      </select>
    </label>
    <label class="md-field pick">
      <span>类型</span>
      <select v-model="task">
        <option v-for="(label, key) in taskLabel" :key="key" :value="key">{{ label }}</option>
      </select>
    </label>
    <button class="md-btn" :disabled="busy" @click="generate">生成报告</button>
  </PageLead>

  <div class="md-grid">
    <section class="md-card span-4 reports">
      <div class="md-card-head">
        <h2>报告列表</h2>
        <span class="meta">{{ list.length }} 份</span>
      </div>
      <div class="rep-list">
        <button
          v-for="r in list"
          :key="r.id"
          class="rep"
          :class="{ 'rep--active': r.id === active }"
          @click="open(r.id)"
        >
          <span class="rep-title">{{ taskLabel[r.task] || '趋势解读' }}，{{ scopeLabel[r.scope] || r.scope }}{{ r.scope_id ? '，' + r.scope_id : '' }}</span>
          <span class="rep-meta">
            {{ fmtDay(r.period_start) }} – {{ fmtDay(r.period_end) }}，{{ r.model }}，
            {{ r.status === 'fallback' ? '规则兜底' : '模型生成' }}，
            {{ r.tokens_in || 0 }}+{{ r.tokens_out || 0 }} tok，{{ fmtTime(r.created_at) }} 生成
          </span>
        </button>
        <p v-if="!list.length" class="md-muted">还没有报告。</p>
      </div>
    </section>

    <section class="md-card span-8 reports">
      <template v-if="detail">
        <div class="ai-head">
          <span class="md-muted small">
            {{ scopeLabel[detail.scope] || detail.scope }}，{{ detail.model }} {{ fmtDay(detail.content.period.from) }} – {{ fmtDay(detail.content.period.to) }}
          </span>
          <span class="md-chip" :class="detail.content.confidence === 'high' ? 'md-chip--ok' : 'md-chip--warn'">
            {{ detail.content.confidence === 'high' ? '高置信' : '规则模板' }}
          </span>
          <button class="md-btn md-btn--text" :disabled="busy" @click="regenerate">按原区间重新生成</button>
        </div>
        <h2 class="headline">{{ detail.content.headline }}</h2>
        <p class="narrative">{{ detail.content.narrative }}</p>

        <div v-if="detail.content.findings?.length" class="md-list">
          <div v-for="(f, i) in detail.content.findings" :key="i" class="md-list-item">
            <span class="sev-tag" :class="f.risk === 'alert' ? 'sev-tag--notice' : 'sev-tag--idle'">
              {{ f.risk === 'alert' ? '告警' : '关注' }}
            </span>
            <div class="md-grow">
              <div>{{ f.label }}</div>
              <div class="md-muted small">{{ f.detail }}</div>
            </div>
          </div>
        </div>

        <ul class="actions">
          <li v-for="(a, i) in detail.content.actions || []" :key="i">{{ a }}</li>
        </ul>
        <p class="md-muted small foot">
          prompt {{ detail.prompt_hash }}，{{ detail.model }}，{{ detail.status === 'fallback' ? '规则兜底' : '模型生成' }}，
          {{ detail.tokens_in || 0 }}+{{ detail.tokens_out || 0 }} tok，{{ fmtDay(detail.period_start) }}
        </p>
      </template>
      <p v-else class="md-muted">选择一份报告查看详情。</p>
    </section>

    <section class="md-card span-6">
      <div class="md-card-head">
        <h2>任务分布</h2>
        <span class="meta">{{ list.length }} 份报告</span>
      </div>
      <ChartBox :option="taskOption" height="180px" label="报告任务分布" />
    </section>

    <section class="md-card span-6">
      <div class="md-card-head">
        <h2>成本与缓存</h2>
        <span class="meta">公有 API 模式才计费</span>
      </div>
      <div class="md-list">
        <div class="md-list-item">
          <div class="md-grow">本月 token<div class="md-muted small">上限 {{ fmtNum(budget, 0) }}</div></div>
          <span class="md-mono">{{ fmtNum(used, 0) }}</span>
        </div>
        <div class="md-list-item">
          <div class="md-grow">缓存命中<div class="md-muted small">同输入 24 h 内复用</div></div>
          <span class="md-mono">{{ cacheHits }}</span>
        </div>
        <div class="md-list-item">
          <div class="md-grow">单次输入上限<div class="md-muted small">超出后先裁剪指标明细</div></div>
          <span class="md-mono">8k</span>
        </div>
        <div class="md-list-item">
          <div class="md-grow">单次输出上限<div class="md-muted small">强约束 JSON</div></div>
          <span class="md-mono">1k</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.error { color: var(--md-error); max-width: var(--content-max); margin: 0 auto var(--space-3); }
.pick { width: 180px; }
.reports {
  display: flex;
  flex-direction: column;
  height: 560px;
  min-width: 0;
}
.rep-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
  min-height: 0;
}
.rep {
  display: grid;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  background: none;
  border: 0;
  border-radius: var(--md-shape-sm);
  cursor: pointer;
  color: var(--md-on-surface);
}
.rep:hover { background: color-mix(in srgb, var(--md-on-surface) 6%, transparent); }
.rep--active { background: var(--md-secondary-container); color: var(--md-on-secondary-container); }
.rep-title { font: var(--md-title-small); }
.rep-meta { font: var(--md-body-small); color: var(--md-on-surface-variant); }
.ai-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.headline { font: var(--md-title-large); margin: var(--space-2) 0; }
.narrative { font: var(--md-body-medium); }
.actions { margin: var(--space-3) 0; padding-left: 18px; color: var(--md-on-surface-variant); }
.foot { border-top: 1px solid var(--md-outline-variant); padding-top: var(--space-2); margin-top: var(--space-3); }
.small { font: var(--md-body-small); }
</style>
