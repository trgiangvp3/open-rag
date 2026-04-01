import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'

const STORAGE_KEY = 'openrag_search_settings'

export const useSearchSettingsStore = defineStore('searchSettings', () => {
  const collection = ref('documents')
  const topK = ref(5)
  const retrievalMode = ref<'semantic' | 'hybrid'>('hybrid')
  const useReranker = ref(true)
  const scoreThreshold = ref<number | null>(null)

  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
    if (saved) {
      collection.value = saved.collection ?? 'documents'
      topK.value = saved.topK ?? 5
      retrievalMode.value = saved.retrievalMode ?? 'hybrid'
      useReranker.value = saved.useReranker ?? true
      scoreThreshold.value = saved.scoreThreshold ?? null
    }
  } catch (e) {
    console.warn('Failed to load search settings:', e)
  }

  const debouncedSave = useDebounceFn(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        collection: collection.value,
        topK: topK.value,
        retrievalMode: retrievalMode.value,
        useReranker: useReranker.value,
        scoreThreshold: scoreThreshold.value,
      }))
    } catch (e) {
      console.warn('Failed to save search settings:', e)
    }
  }, 300)

  watch([collection, topK, retrievalMode, useReranker, scoreThreshold], debouncedSave)

  return { collection, topK, retrievalMode, useReranker, scoreThreshold }
})
