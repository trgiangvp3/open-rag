<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { health } from './api'
import { useCollectionsStore } from './stores/collections'
import SearchTab from './components/SearchTab.vue'
import UploadTab from './components/UploadTab.vue'
import DocumentsTab from './components/DocumentsTab.vue'
import CollectionsTab from './components/CollectionsTab.vue'

const tab = ref<'search' | 'upload' | 'documents' | 'collections'>('search')
const healthy = ref<boolean | null>(null)
const store = useCollectionsStore()

onMounted(async () => {
  store.fetch()
  try {
    const { data } = await health()
    healthy.value = data.status === 'ok'
  } catch {
    healthy.value = false
  }
})

const tabs = [
  { id: 'search', label: 'Tìm kiếm' },
  { id: 'upload', label: 'Upload' },
  { id: 'documents', label: 'Tài liệu' },
  { id: 'collections', label: 'Collections' },
] as const
</script>

<template>
  <div class="min-h-screen bg-slate-900 text-slate-100">
    <!-- Header -->
    <header class="border-b border-slate-700 px-6 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <h1 class="text-lg font-semibold text-violet-400">OpenRAG</h1>
        <span class="text-slate-600 text-sm">Document Search</span>
      </div>
      <div class="flex items-center gap-2 text-xs">
        <div :class="['w-2 h-2 rounded-full', healthy === null ? 'bg-slate-500' : healthy ? 'bg-green-500' : 'bg-red-500']" />
        <span class="text-slate-500">{{ healthy === null ? 'Kiểm tra...' : healthy ? 'Online' : 'ML service offline' }}</span>
      </div>
    </header>

    <!-- Tabs -->
    <nav class="border-b border-slate-700 px-6">
      <div class="flex gap-1">
        <button
          v-for="t in tabs"
          :key="t.id"
          @click="tab = t.id"
          :class="[
            'px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px',
            tab === t.id
              ? 'border-violet-500 text-violet-400'
              : 'border-transparent text-slate-500 hover:text-slate-300'
          ]"
        >
          {{ t.label }}
        </button>
      </div>
    </nav>

    <!-- Content -->
    <main class="max-w-5xl mx-auto px-6 py-6">
      <SearchTab v-if="tab === 'search'" />
      <UploadTab v-else-if="tab === 'upload'" />
      <DocumentsTab v-else-if="tab === 'documents'" />
      <CollectionsTab v-else-if="tab === 'collections'" />
    </main>
  </div>
</template>
