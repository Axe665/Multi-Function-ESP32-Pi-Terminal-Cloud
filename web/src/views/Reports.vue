<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtDay, fmtNum } from '../api'
import PageHeader from '../components/PageHeader.vue'
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
  <PageHeader title="AI 报告" sub="默认使用本地 Ollama。模型不可用时落规则模板，置信度标为低。">
    <select v-model="scope" class="md-field" aria-label="报告范围">
      <option value="fleet">全网</option>
      <option value="edge">单边</option>
    </select>
    <select v-model="task" class="md-field" aria-label="报告类型">
      <option v-for="(label, key) in taskLabel" :key="key" :value="key">{{ label }}</option>
    </select>
    <button class="md-btn" :disabled="busy" @click="generate">生成报告</button>
  </PageHeader>

  <p v-if="error" class="error">{{ error }}</p>

  <div class="split">
    <section class="md-card">
      <div class="section-head">
        <h2>报告列表</h2>
        <span class="md-muted">{{ list.length }} 份</span>
      </div>
      <table class="md-table">
        <thead><tr><th>范围</th><th>周期</th><th>模型</th><th class="num">token</th></tr></thead>
        <tbody>
          <tr v-for="r in list" :key="r.id" :class="{ 'row--active': r.id === active }" @click="open(r.id)" tabindex="0"
              @keydown.enter="open(r.id)">
            <td>
              <strong>{{ scopeLabel[r.scope] || r.scope }}</strong>
              <div class="md-mono md-muted">{{ r.scope_id || '全部' }}</div>
              <div class="md-muted small">{{ taskLabel[r.task] || '趋势解读' }}</div>
            </td>
            <td>{{ fmtDay(r.period_start) }} – {{ fmtDay(r.period_end) }}</td>
            <td class="md-muted">{{ r.model }}</td>
            <td class="num">{{ r.model === 'rules-v1' ? '—' : `${fmtNum(r.tokens_in, 0)} / ${fmtNum(r.tokens_out, 0)}` }}</td>
          </tr>
          <tr v-if="!list.length"><td colspan="4" class="md-muted">还没有报告。</td></tr>
        </tbody>
      </table>
    </section>

    <section class="md-card">
      <template v-if="detail">
        <div class="ai-head">
          <span class="md-muted small">
            {{ scopeLabel[detail.scope] || detail.scope }}，{{ detail.model }} {{ fmtDay(detail.content.period.from) }} – {{ fmtDay(detail.content.period.to) }}
          </span>
          <button class="md-btn md-btn--text" :disabled="busy" @click="regenerate">按原区间重新生成</button>
          <span class="md-chip" :class="detail.content.confidence === 'high' ? 'md-chip--ok' : 'md-chip--warn'">
            {{ detail.content.confidence === 'high' ? '高置信' : '规则模板' }}
          </span>
        </div>
        <h2 class="headline">{{ detail.content.headline }}</h2>
        <p class="narrative">{{ detail.content.narrative }}</p>

        <div v-if="detail.content.findings?.length" class="md-list">
          <div v-for="(f, i) in detail.content.findings" :key="i" class="md-list-row">
            <span class="md-chip" :class="f.risk === 'alert' ? 'md-chip--alert' : 'md-chip--warn'">{{ f.risk }}</span>
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
          prompt {{ detail.prompt_hash }}，{{ detail.model }}，{{ fmtDay(detail.period_start) }}
        </p>
      </template>
      <p v-else class="md-muted">选择一份报告查看详情。</p>
    </section>
  </div>

  <div class="split-2">
    <section class="md-card">
      <div class="section-head"><h2>任务分布</h2><span class="md-muted">{{ list.length }} 份报告</span></div>
      <div class="md-list">
        <div v-for="t in byTask" :key="t.key" class="md-list-row">
          <div class="md-grow">
            <div>{{ t.label }}</div>
            <div class="md-mono md-muted">{{ t.key }}</div>
          </div>
          <span class="md-mono">{{ t.n }}</span>
        </div>
      </div>
    </section>

    <section class="md-card">
      <div class="section-head"><h2>成本与缓存</h2><span class="md-muted">公有 API 模式才计费</span></div>
      <div class="md-list">
        <div class="md-list-row"><div class="md-grow">本月 token<div class="md-muted small">上限 {{ fmtNum(budget, 0) }}</div></div><span class="md-mono">{{ fmtNum(used, 0) }}</span></div>
        <div class="md-list-row"><div class="md-grow">缓存命中<div class="md-muted small">同输入 24 h 内复用</div></div><span class="md-mono">{{ cacheHits }}</span></div>
        <div class="md-list-row"><div class="md-grow">单次输入上限<div class="md-muted small">超出后先裁剪指标明细</div></div><span class="md-mono">8k</span></div>
        <div class="md-list-row"><div class="md-grow">单次输出上限<div class="md-muted small">强约束 JSON</div></div><span class="md-mono">1k</span></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.split {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
  gap: var(--space-4); align-items: start;
}
.split-2 {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4); margin-top: var(--space-4); align-items: start;
}
.section-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: var(--space-3); }
.row--active { background: var(--md-secondary-container); }
tbody tr { cursor: pointer; }
.ai-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space-2); }
.headline { font: var(--md-title-large); margin: var(--space-2) 0; }
.narrative { font: var(--md-body-medium); }
.actions { margin: var(--space-3) 0; padding-left: 18px; color: var(--md-on-surface-variant); }
.foot { border-top: 1px solid var(--md-outline-variant); padding-top: var(--space-2); margin-top: var(--space-3); }
.small { font: var(--md-body-small); }
@media (max-width: 1000px) {
  .split, .split-2 { grid-template-columns: minmax(0, 1fr); }
}
</style>
