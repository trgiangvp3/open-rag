<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/theme'

const router = useRouter()
const auth = useAuthStore()
const themeStore = useThemeStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function handleLogin() {
  if (!username.value.trim() || !password.value) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    router.replace('/')
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } | string }; message?: string }
    const msg = typeof err.response?.data === 'string' ? err.response.data : (err.response?.data as any)?.message
    error.value = msg ?? err.message ?? 'Đăng nhập thất bại'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center" style="background: var(--bg-secondary)">
    <div class="w-full max-w-sm px-6">
      <!-- Logo -->
      <div class="text-center mb-8">
        <h1 class="text-3xl font-semibold th-accent mb-1">OpenRAG</h1>
        <p class="th-text3 text-sm">Tra cứu văn bản pháp luật thông minh</p>
      </div>

      <!-- Login card -->
      <div class="rounded-xl border p-6" style="background: var(--bg-elevated); border-color: var(--border-primary); box-shadow: var(--shadow-lg)">
        <h2 class="th-text text-lg font-semibold mb-6 text-center">Đăng nhập</h2>

        <form @submit.prevent="handleLogin" class="space-y-4">
          <div class="space-y-1.5">
            <label class="th-text2 text-xs font-medium">Tên đăng nhập</label>
            <input v-model="username" type="text" autocomplete="username" autofocus
              placeholder="username"
              class="w-full rounded-lg border px-4 py-2.5 text-sm focus:outline-none transition-colors"
              style="background: var(--bg-input); border-color: var(--border-primary); color: var(--text-primary)"
              onfocus="this.style.borderColor='var(--border-accent)'"
              onblur="this.style.borderColor='var(--border-primary)'" />
          </div>

          <div class="space-y-1.5">
            <label class="th-text2 text-xs font-medium">Mật khẩu</label>
            <input v-model="password" type="password" autocomplete="current-password"
              placeholder="••••••••"
              class="w-full rounded-lg border px-4 py-2.5 text-sm focus:outline-none transition-colors"
              style="background: var(--bg-input); border-color: var(--border-primary); color: var(--text-primary)"
              onfocus="this.style.borderColor='var(--border-accent)'"
              onblur="this.style.borderColor='var(--border-primary)'" />
          </div>

          <!-- Error -->
          <div v-if="error" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2 text-red-600 dark:text-red-400 text-xs">{{ error }}</div>

          <button type="submit" :disabled="loading || !username.trim() || !password"
            class="w-full py-2.5 rounded-lg text-sm font-medium transition-all disabled:opacity-40"
            style="background: var(--accent); color: var(--accent-text)">
            {{ loading ? 'Đang đăng nhập...' : 'Đăng nhập' }}
          </button>
        </form>
      </div>

      <div class="flex items-center justify-center mt-4">
        <button @click="themeStore.toggle()" class="th-text3 text-xs hover:th-text2 transition-colors flex items-center gap-1">
          <svg v-if="themeStore.theme === 'dark'" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
          <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
          </svg>
          {{ themeStore.theme === 'dark' ? 'Chế độ sáng' : 'Chế độ tối' }}
        </button>
      </div>

      <p class="th-text3 text-xs text-center mt-3">Liên hệ quản trị viên để được cấp tài khoản</p>
    </div>
  </div>
</template>
