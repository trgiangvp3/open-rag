import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'search',
      component: () => import('./components/SearchTab.vue'),
    },
    {
      path: '/documents',
      name: 'documents',
      component: () => import('./components/DocumentsTab.vue'),
      meta: { requiresAdmin: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('./components/SettingsTab.vue'),
      meta: { requiresAdmin: true },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('./components/LoginPage.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

export default router
