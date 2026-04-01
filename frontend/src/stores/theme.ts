import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'openrag_theme'

export const useThemeStore = defineStore('theme', () => {
  const saved = localStorage.getItem(STORAGE_KEY) as Theme | null
  const theme = ref<Theme>(saved ?? 'dark')

  function apply(t: Theme) {
    document.documentElement.classList.toggle('dark', t === 'dark')
  }

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  watch(theme, (t) => {
    localStorage.setItem(STORAGE_KEY, t)
    apply(t)
  }, { immediate: true })

  return { theme, toggle }
})
