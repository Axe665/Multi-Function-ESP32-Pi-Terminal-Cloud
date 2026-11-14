import { createRouter, createWebHashHistory } from 'vue-router'

// meta.title 是顶栏标题，meta.icon 是导航 rail 的 Material Symbols 图标
export const routes = [
  { path: '/', component: () => import('./views/Overview.vue'), meta: { title: '总览', nav: '总览', icon: 'dashboard' } },
  { path: '/edges', component: () => import('./views/Edges.vue'), meta: { title: '边与设备', nav: '边与设备', icon: 'hub' } },
  { path: '/devices/:id', component: () => import('./views/DeviceDetail.vue'), meta: { title: '设备详情', nav: '边与设备', icon: 'devices' } },
  { path: '/compare', component: () => import('./views/Compare.vue'), meta: { title: '对比', nav: '对比', icon: 'compare_arrows' } },
  { path: '/reports', component: () => import('./views/Reports.vue'), meta: { title: 'AI 报告', nav: 'AI 报告', icon: 'summarize' } },
  { path: '/privacy', component: () => import('./views/Privacy.vue'), meta: { title: '隐私审计', nav: '隐私审计', icon: 'privacy_tip' } },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
