<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { health } from './api'
import { useCollectionsStore } from './stores/collections'
import { useAuthStore } from './stores/auth'
import { useThemeStore } from './stores/theme'

const router = useRouter()
const route = useRoute()
const healthy = ref<boolean | null>(null)
const store = useCollectionsStore()
const auth = useAuthStore()
const themeStore = useThemeStore()

onMounted(async () => {
  await auth.init()
  if (!auth.isLoggedIn) {
    if (route.name !== 'login') router.replace('/login')
    return
  }
  if (route.name === 'login') router.replace('/')
  store.fetch()
  try {
    const { data } = await health()
    healthy.value = data.status === 'ok'
  } catch {
    healthy.value = false
  }
})

// Guard: redirect unauthenticated users
watch(() => auth.isLoggedIn, (loggedIn) => {
  if (!loggedIn && route.name !== 'login') router.replace('/login')
})

// Guard: redirect non-admin from admin routes
watch(() => route.name, () => {
  if (route.meta.requiresAdmin && !auth.isAdmin) router.replace('/')
})

const navItems = [
  { to: '/', label: 'Tìm kiếm', icon: 'search', adminOnly: false },
  { to: '/documents', label: 'Tài liệu', icon: 'docs', adminOnly: true },
  { to: '/users', label: 'Người dùng', icon: 'users', adminOnly: true },
  { to: '/settings', label: 'Cài đặt', icon: 'settings', adminOnly: true },
]

function logout() {
  auth.logout()
  router.replace('/login')
}
</script>

<template>
  <!-- Login page (no shell) -->
  <router-view v-if="!auth.isLoggedIn" />

  <!-- Main app shell -->
  <div v-else class="min-h-screen th-bg th-text" style="transition: background 0.2s, color 0.2s">
    <!-- Top bar — Office ribbon-style -->
    <header class="th-elevated th-border border-b px-5 py-0 flex items-center justify-between h-12"
      style="background: var(--bg-elevated)">
      <div class="flex items-center gap-1 h-full">
        <router-link to="/" class="th-accent font-semibold text-base px-2 hover:opacity-80 transition-opacity flex items-center h-full">
          <svg class="w-5 h-5 mr-2 opacity-80" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m5.231 13.481L15 17.25m-4.5-15H5.625c-.621 0-1.125.504-1.125 1.125v16.5c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9zm3.75 11.625a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"/></svg>
          OpenRAG
        </router-link>

        <nav class="flex items-center h-full ml-2">
          <template v-for="item in navItems" :key="item.to">
            <router-link v-if="!item.adminOnly || auth.isAdmin" :to="item.to"
              :class="[
                'px-3 h-full flex items-center text-sm font-medium transition-colors border-b-2',
                route.path === item.to
                  ? 'border-[var(--accent)] th-accent'
                  : 'border-transparent th-text2 hover:th-text'
              ]"
              style="margin-bottom: -1px">
              {{ item.label }}
            </router-link>
          </template>
        </nav>
      </div>

      <div class="flex items-center gap-3">
        <!-- Status dot -->
        <div class="flex items-center gap-1.5 text-xs th-text3">
          <div :class="['w-1.5 h-1.5 rounded-full', healthy === null ? 'bg-gray-400' : healthy ? 'bg-green-500' : 'bg-red-500']" />
          <span>{{ healthy === null ? '' : healthy ? 'Online' : 'Offline' }}</span>
        </div>

        <!-- Theme toggle -->
        <button @click="themeStore.toggle()"
          class="w-8 h-8 rounded-lg th-hover flex items-center justify-center th-text2 transition-colors"
          :title="themeStore.theme === 'dark' ? 'Chuyển sang sáng' : 'Chuyển sang tối'">
          <!-- Sun icon (show when dark) -->
          <svg v-if="themeStore.theme === 'dark'" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
          <!-- Moon icon (show when light) -->
          <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
          </svg>
        </button>

        <!-- User info -->
        <div class="flex items-center gap-2 pl-3" style="border-left: 1px solid var(--border-primary)">
          <div class="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
            style="background: var(--accent-light); color: var(--text-accent)">
            {{ auth.user?.displayName?.[0]?.toUpperCase() || 'U' }}
          </div>
          <span class="th-text2 text-sm">{{ auth.user?.displayName }}</span>
          <button @click="logout" class="th-text3 hover:th-text text-xs ml-1 transition-colors">Đăng xuất</button>
        </div>
      </div>
    </header>

    <main :class="route.path === '/' ? '' : 'px-6 py-6'">
      <router-view v-slot="{ Component }">
        <keep-alive include="SearchTab">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>
