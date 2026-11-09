<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtNum, fmtTime } from '../api'
import PageHeader from '../components/PageHeader.vue'

const audit = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    audit.value = await api('/privacy/audit')
  } catch (e) {
    error.value = e.message
  }
})

const kRows = computed(() => {
  const dist = audit.value?.k_distribution || {}
  const max = Math.max(1, ...Object.values(dist))
  return Object.entries(dist).map(([label, n]) => ({
    label: label === '5' ? 'k = 5' : `k = ${label}`,
    n,
    pct: Math.round((n / max) * 100),
  }))
})
</script>

<template>
  <PageHeader
    title="隐私审计"
    :sub="`云端到底拿到了什么？只存日聚合、建议文本和计数。保留 ${audit?.retention_days ?? '—'} 天。`"
  >
    <span class="md-chip">门槛 k ≥ {{ audit?.k_min ?? 5 }}</span>
  </PageHeader>

  <p v-if="error" class="error">{{ error }}</p>

  <section class="kpis" v-if="audit">
    <div class="kpi"><div class="label">观测到的最小 k</div><div class="value">{{ audit.k_min_seen }}</div><div class="note">门槛 {{ audit.k_min }}</div></div>
    <div class="kpi"><div class="label">被抑制的桶</div><div class="value">{{ fmtNum(audit.suppressed_buckets, 0) }}</div><div class="note">k&lt;5，从未离开端</div></div>
    <div class="kpi"><div class="label">原始字段拦截</div><div class="value">{{ audit.raw_rejected }}</div><div class="note">摄取时直接 422</div></div>
    <div class="kpi"><div class="label">已入库行</div><div class="value">{{ fmtNum(audit.rows_ingested, 0) }}</div><div class="note">全部是日聚合</div></div>
    <div class="kpi"><div class="label">导出记录</div><div class="value">{{ audit.exports?.length || 0 }}</div><div class="note">报告导出全部留痕</div></div>
  </section>

  <div class="duo">
    <section class="md-card">
      <div class="section-head">
        <h2>k 值分布</h2>
        <span class="md-muted">{{ audit?.rows ?? 0 }} 行日聚合</span>
      </div>
      <div class="bars">
        <div v-for="r in kRows" :key="r.label" class="bar">
          <span class="bar-label">{{ r.label }}</span>
          <span class="bar-track"><span class="bar-fill" :style="{ width: r.pct + '%' }"></span></span>
          <span class="bar-num">{{ r.n }}</span>
        </div>
      </div>
      <p class="md-muted small">
        分布里没有低于 5 的桶，因为低于门槛的行在端侧就被丢弃了；边只上报抑制计数。
      </p>
    </section>

    <section class="md-card">
      <div class="section-head">
        <h2>字段白名单</h2>
        <span class="md-muted">摄取时强校验</span>
      </div>
      <div class="field-group">
        <h3 class="md-muted">保留</h3>
        <p class="tags">
          <span v-for="f in audit?.fields_kept || []" :key="f" class="md-chip">{{ f }}</span>
        </p>
      </div>
      <div class="field-group">
        <h3 class="md-muted">拒收</h3>
        <p class="tags">
          <span v-for="f in audit?.fields_rejected || []" :key="f" class="md-chip md-chip--alert">{{ f }}</span>
        </p>
      </div>
      <p class="md-muted small">
        载荷里出现任一个拒收字段，整份报表被拒收并记一次拦截，不做「过滤后放行」。
      </p>
    </section>
  </div>

  <section class="md-card log">
    <div class="section-head">
      <h2>导出记录</h2>
      <span class="md-muted">{{ audit?.exports?.length || 0 }} 条</span>
    </div>
    <table class="md-table">
      <thead><tr><th>时间</th><th>动作</th><th>对象</th></tr></thead>
      <tbody>
        <tr v-for="a in audit?.exports || []" :key="'x' + a.id">
          <td class="md-mono">{{ fmtTime(a.created_at, true) }}</td>
          <td>{{ a.action }}</td>
          <td class="md-mono">{{ a.target }}</td>
        </tr>
        <tr v-if="!audit?.exports?.length"><td colspan="3" class="md-muted">还没有导出记录。</td></tr>
      </tbody>
    </table>

    <div class="section-head log-head">
      <h2>审计日志</h2>
      <span class="md-muted">最近 {{ audit?.audit?.length || 0 }} 条</span>
    </div>
    <table class="md-table">
      <thead><tr><th>时间</th><th>动作</th><th>对象</th><th>详情</th></tr></thead>
      <tbody>
        <tr v-for="a in audit?.audit || []" :key="a.id">
          <td class="md-mono">{{ fmtTime(a.created_at, true) }}</td>
          <td>{{ a.action }}</td>
          <td class="md-mono">{{ a.target }}</td>
          <td class="md-muted detail">{{ typeof a.detail === 'string' ? a.detail : JSON.stringify(a.detail) }}</td>
        </tr>
        <tr v-if="!audit?.audit?.length"><td colspan="4" class="md-muted">还没有审计记录。</td></tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1px;
  background: var(--md-surface-container-highest); border: 1px solid var(--md-surface-container-highest);
  border-radius: var(--md-shape-md); overflow: hidden; margin-bottom: var(--space-4);
}
.kpi { background: var(--md-surface-container-low); padding: var(--space-3) var(--space-4); }
.label { font: var(--md-label-medium); color: var(--md-on-surface-variant); }
.value { font: var(--md-headline-small); }
.note { font: var(--md-body-small); color: var(--md-on-surface-variant); }
.duo {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4); align-items: start;
}
.section-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: var(--space-3); gap: var(--space-3); }
.bars { display: flex; flex-direction: column; gap: var(--space-2); }
.bar { display: grid; grid-template-columns: 64px minmax(0, 1fr) 48px; align-items: center; gap: var(--space-2); }
.bar-label { font: var(--md-label-medium); color: var(--md-on-surface-variant); }
.bar-track { height: 8px; border-radius: var(--md-shape-full); background: var(--md-surface-container-highest); overflow: hidden; }
.bar-fill { display: block; height: 100%; background: var(--md-primary); }
.bar-num { text-align: right; font: var(--md-mono); color: var(--md-on-surface-variant); }
.field-group { margin-top: var(--space-3); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 0; }
.small { font: var(--md-body-small); }
.log { margin-top: var(--space-4); }
.log-head { margin-top: var(--space-5); }
.detail { max-width: 420px; overflow-wrap: anywhere; }
</style>
