import { createRouter, createWebHashHistory } from 'vue-router'

export const routes = [
  { path: '/', component: () => import('./views/Overview.vue') },
  { path: '/edges', component: () => import('./views/Edges.vue') },
  { path: '/devices/:id', component: () => import('./views/DeviceDetail.vue') },
  { path: '/compare', component: () => import('./views/Compare.vue') },
  { path: '/reports', component: () => import('./views/Reports.vue') },
  { path: '/privacy', component: () => import('./views/Privacy.vue') },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})
