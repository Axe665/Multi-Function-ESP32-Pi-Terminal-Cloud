<script setup>
import { computed } from 'vue'

const props = defineProps({
  edges: { type: Array, default: () => [] },
  cloudName: { type: String, default: '' },
})

// 位置固定：第 i 个边永远在同一行，翻页回来还能按位置找到它
const rows = computed(() =>
  props.edges.map((e) => {
    const mins = e.last_seen ? Math.max(0, Math.round((Date.now() - e.last_seen) / 60000)) : null
    const fresh = mins !== null && mins < 120
    return {
      ...e,
      fresh,
      // 2 小时是云的在线口径
      ago: mins === null ? '没有上报' : mins < 60 ? `${mins} 分钟前上报` : `${Math.round(mins / 60)} 小时前上报`,
    }
  }),
)
</script>

<template>
  <div class="topo">
    <div class="hub">
      <span class="hub-dot" aria-hidden="true"></span>
      <span class="hub-label">本机</span>
      <span class="hub-name">{{ cloudName }}</span>
    </div>

    <ul class="rows">
      <li v-for="e in rows" :key="e.edge_id" class="row" :class="{ 'row--on': e.fresh }">
        <div class="row-main">
          <div class="row-meta">
            <strong>{{ e.name }}</strong>
            <span class="md-mono md-muted">{{ e.edge_id }}</span>
          </div>
          <div class="row-stat">
            <span class="cells" :aria-label="`${e.online_devices} / ${e.devices} 台在线`">
              <span v-for="i in e.devices" :key="i" class="cell" :class="{ 'cell--on': i <= e.online_devices }"></span>
            </span>
            <span class="count">{{ e.online_devices }} / {{ e.devices }} 台在线</span>
          </div>
        </div>
        <div class="row-foot">
          <span class="md-chip" :class="e.fresh ? 'md-chip--ok' : 'md-chip--idle'">{{ e.fresh ? '在线' : '离线' }}</span>
          <span>{{ e.ago }}</span>
        </div>
      </li>
      <li v-if="!rows.length" class="md-muted">还没有边节点上报。</li>
    </ul>
  </div>
</template>

<style scoped>
.topo {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 0 var(--space-4);
  align-items: center;
}
.hub {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: var(--space-3) var(--space-2);
  border: 1px solid var(--md-card-border);
  border-radius: var(--md-shape-md);
  background: var(--md-surface-container-high);
  text-align: center;
}
.hub-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--md-primary);
}
.hub-label {
  font: var(--md-title-small);
  color: var(--md-on-surface);
}
.hub-name {
  font: var(--md-label-small);
  color: var(--md-on-surface-variant);
}

/* 一条竖线把三条边接回本机：线的存在就是拓扑关系本身 */
.rows {
  list-style: none;
  margin: 0;
  padding: 0 0 0 var(--space-4);
  border-left: 1px solid var(--md-outline-variant);
  display: grid;
  gap: var(--space-3);
}
.row {
  position: relative;
}
/* 横向支线 */
.row::before {
  content: '';
  position: absolute;
  left: calc(-1 * var(--space-4));
  top: 14px;
  width: var(--space-4);
  border-top: 1px solid var(--md-outline-variant);
}
.row--on::before {
  border-top-color: var(--md-primary);
}
.row-footer,
.row-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 4px;
  font: var(--md-body-small);
  color: var(--md-on-surface-variant);
}
.row-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.row-meta {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}
.row-stat {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.cells {
  display: inline-flex;
  gap: 4px;
}
.cell {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  border: 1px solid var(--md-outline-variant);
}
.cell--on {
  background: var(--md-primary);
  border-color: var(--md-primary);
}
.count {
  font: var(--md-label-medium);
  color: var(--md-on-surface-variant);
  white-space: nowrap;
}

@media (max-width: 720px) {
  .topo {
    grid-template-columns: 1fr;
    gap: var(--space-3);
  }
  .rows {
    border-left: 0;
    padding-left: 0;
  }
  .row::before {
    display: none;
  }
}
</style>
