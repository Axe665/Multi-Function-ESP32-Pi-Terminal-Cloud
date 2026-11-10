<script setup>
/**
 * 全网拓扑：本机在中心，边围一圈，设备挂在各自的边上。
 * 边名常显、设备只在 hover 时出名字——13 个标签全铺开在 1440 下会互相压。
 * 用 ECharts 的 graph 而不是 canvas 星座：能缩放、能拖、能点，且跟着主题换色。
 */
import { computed } from 'vue'
import ChartBox from './ChartBox.vue'
import { chartTokens, useTheme } from '../lib/theme'

const props = defineProps({
  edges: { type: Array, default: () => [] },
  devices: { type: Array, default: () => [] },
  cloudName: { type: String, default: '' },
})
const emit = defineEmits(['pick'])
const { theme } = useTheme()
const tk = computed(() => chartTokens(theme.value))

const ago = (ms) => {
  if (!ms) return '没有上报'
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60000))
  return mins < 60 ? `${mins} 分钟前上报` : `${Math.round(mins / 60)} 小时前上报`
}

const option = computed(() => {
  const t = tk.value
  const edges = props.edges || []
  const byEdge = new Map(edges.map((e) => [e.edge_id, []]))
  for (const d of props.devices || []) {
    if (!byEdge.has(d.edge_id)) byEdge.set(d.edge_id, [])
    byEdge.get(d.edge_id).push(d)
  }

  const nodes = [{
    id: 'cloud', name: props.cloudName || '本机', kind: 'cloud', x: 0, y: 0,
    symbolSize: 46, itemStyle: { color: t.text },
    label: { show: true, position: 'bottom', distance: 16, backgroundColor: t.border, padding: [2, 4], borderRadius: 3 },
  }]
  const links = []
  const n = Math.max(1, edges.length)
  const R = 300

  edges.forEach((e, i) => {
    const ang = (i / n) * Math.PI * 2 - Math.PI / 2
    const x = Math.cos(ang) * R
    const y = Math.sin(ang) * R * 0.40
    const online = e.online_devices > 0
    nodes.push({
      id: `edge:${e.edge_id}`, name: e.name || e.edge_id, kind: 'edge', edge_id: e.edge_id,
      x, y, symbolSize: 34,
      itemStyle: { color: online ? t.line : t.axis },
      label: {
        show: true,
        position: x >= 6 ? 'right' : x <= -6 ? 'left' : y >= 0 ? 'top' : 'bottom',
        distance: 8, backgroundColor: t.border, padding: [2, 4], borderRadius: 3,
      },
      _meta: e,
    })
    links.push({
      source: 'cloud', target: `edge:${e.edge_id}`,
      lineStyle: { width: 2, color: online ? t.line : t.split, opacity: online ? 0.9 : 0.5 },
    })

    const kids = byEdge.get(e.edge_id) || []
    const k = Math.max(1, kids.length)
    kids.forEach((d, j) => {
      const a2 = ang + (j - (k - 1) / 2) * 0.42
      const r2 = 78 + (j % 2) * 14
      nodes.push({
        id: `device:${d.device_id}`, name: d.device_id, kind: 'device', edge_id: e.edge_id,
        device_id: d.device_id, x: x + Math.cos(a2) * r2, y: y + Math.sin(a2) * r2 * 0.55,
        symbolSize: 14,
        itemStyle: { color: d.online ? t.palette[1] : t.axis, opacity: d.online ? 1 : 0.55 },
        label: { show: false },
        _meta: d,
      })
      links.push({
        source: `edge:${e.edge_id}`, target: `device:${d.device_id}`,
        lineStyle: { width: 1, color: t.split, opacity: 0.9 },
      })
    })
  })

  return {
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (p) => {
        if (p.dataType === 'edge') return p.data.name
        const d = p.data
        if (d.kind === 'cloud') return `${d.name}<br/>私有云 · 单租户`
        if (d.kind === 'edge') {
          const e = d._meta || {}
          const on = `${e.online_devices || 0} / ${e.devices || 0} 台在线`
          return `${e.name || d.name}<br/>${e.edge_id}<br/>${on}，${ago(e.last_seen)}`
        }
        return `${d.device_id}<br/>${d.edge_id}<br/>${d.online ? '在线' : '离线'}`
      },
    },
    series: [{
      type: 'graph',
      layout: 'none',
      roam: true,
      scaleLimit: { min: 0.6, max: 3 },
      draggable: true,
      data: nodes,
      links,
      edgeSymbol: ['none', 'none'],
      label: {
        show: false,
        color: t.text,
        fontFamily: 'inherit',
        fontSize: 14,
        formatter: (p) => p.data.name,
      },
      emphasis: {
        focus: 'adjacency',
        label: { show: true },
        lineStyle: { width: 3, opacity: 1 },
      },
      lineStyle: { curveness: 0.06 },
    }],
  }
})

function pick(p) {
  const d = p?.data
  if (!d || d.kind === 'cloud') return
  emit('pick', d.kind, d)
}
</script>

<template>
  <ChartBox :option="option" height="100%" :on-click="pick" />
</template>
