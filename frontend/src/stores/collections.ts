import { defineStore } from 'pinia'
import { ref } from 'vue'
import { listCollections, type CollectionInfo } from '../api'

export const useCollectionsStore = defineStore('collections', () => {
  const collections = ref<CollectionInfo[]>([])
  const loading = ref(false)

  async function fetch() {
    loading.value = true
    try {
      const { data } = await listCollections()
      collections.value = data
    } finally {
      loading.value = false
    }
  }

  return { collections, loading, fetch }
})
