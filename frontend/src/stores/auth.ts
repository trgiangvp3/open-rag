import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, TOKEN_KEY, setUnauthorizedHandler } from '../api'

export interface AuthUser {
  id: number
  username: string
  displayName: string
  role: 'admin' | 'user'
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')

  function setToken(t: string) {
    token.value = t
    localStorage.setItem(TOKEN_KEY, t)
    api.defaults.headers.common['Authorization'] = `Bearer ${t}`
  }

  function clearAuth() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    delete api.defaults.headers.common['Authorization']
  }

  async function init() {
    // Register reactive logout handler for 401 responses
    setUnauthorizedHandler(() => clearAuth())

    const saved = localStorage.getItem(TOKEN_KEY)
    if (!saved) return
    token.value = saved
    api.defaults.headers.common['Authorization'] = `Bearer ${saved}`
    try {
      const { data } = await api.get<AuthUser>('/auth/me')
      user.value = data
    } catch {
      clearAuth()
    }
  }

  async function login(username: string, password: string) {
    const { data } = await api.post<{ token: string; user: AuthUser }>('/auth/login', { username, password })
    setToken(data.token)
    user.value = data.user
  }

  function logout() {
    clearAuth()
  }

  return { token, user, isLoggedIn, isAdmin, init, login, logout }
})
