<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api'
import PageHeader from '../components/PageHeader.vue'

const edges = ref([])
const devices = ref([])
const activeEdge = ref('')
const error = ref('')

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
  <PageHeader
    title="边与设备"
    :sub="`${edges.length} 个边、${devices.length} 台设备。点边看设备，点设备看趋势。`"
  >
    <span class="md-chip">{{ devices.filter((d) => d.online).length }} 台在线</span>
    <button v-if="activeEdge" class="md-btn md-btn--text" @click="activeEdge = ''">显示全部边</button>
  </PageHeader>

  <p v-if="error" class="error">{{ error }}</p>

  <section class="md-card">
    <div class="section-head">
      <h2>边节点</h2>
      <span class="md-muted">最近 7 天</span>
    </div>
    <table class="md-table">
      <thead>
        <tr><th>边</th><th class="num">设备</th><th class="num">在线</th><th class="num">有数据天数</th><th class="num">最小 k</th><th class="num">指标数</th></tr>
      </thead>
      <tbody>
        <tr v-for="e in edges" :key="e.edge_id" class="row-click" :class="{ 'row--active': activeEdge === e.edge_id }"
            tabindex="0" @click="activeEdge = e.edge_id" @keydown.enter="activeEdge = e.edge_id">
          <td>
            <strong>{{ e.name }}</strong>
            <div class="md-mono md-muted">{{ e.edge_id }}</div>
          </td>
          <td class="num">{{ e.devices }}</td>
          <td class="num">{{ e.online_devices }}</td>
          <td class="num">{{ e.days }}<span class="md-muted"> / 7</span></td>
          <td class="num">{{ e.k_min }}</td>
          <td class="num">{{ e.metrics.length }}</td>
        </tr>
      </tbody>
    </table>
  </section>

  <section class="md-card devices">
    <div class="section-head">
      <h2>设备</h2>
      <span class="md-muted">{{ activeEdge ? nameOf(activeEdge) + '，' : '' }}{{ shown.length }} 台</span>
    </div>
    <table class="md-table">
      <thead>
        <tr><th>设备</th><th>所属边</th><th>场景</th><th>状态</th><th class="num">有数据天数</th><th class="num">最小 k</th></tr>
      </thead>
      <tbody>
        <tr v-for="d in shown" :key="d.device_id">
          <td class="md-mono"><RouterLink :to="`/devices/${d.device_id}`">{{ d.device_id }}</RouterLink></td>
          <td>{{ nameOf(d.edge_id) }}</td>
          <td>{{ d.scene || '—' }}</td>
          <td>
            <span class="md-chip" :class="d.online ? 'md-chip--ok' : 'md-chip--warn'">
              {{ d.online ? '在线' : '离线' }}
            </span>
          </td>
          <td class="num">{{ d.days }}<span class="md-muted"> / 7</span></td>
          <td class="num">{{ d.k_min }}</td>
        </tr>
        <tr v-if="!shown.length"><td colspan="6" class="md-muted">这个边还没有设备上报。</td></tr>
      </tbody>
    </table>
    <p class="md-muted small hint">最近一次上报 {{ fmtTime(Math.max(...devices.map((d) => d.last_seen || 0))) }}。</p>
  </section>
</template>

<style scoped>
.error { color: var(--md-error); margin-bottom: var(--space-3); }
.section-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: var(--space-3); }
.devices { margin-top: var(--space-4); }
.row-click { cursor: pointer; }
.row--active { background: var(--md-secondary-container); }
.small { font: var(--md-body-small); }
.hint { margin-top: var(--space-3); }
</style>
