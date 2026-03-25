<script setup lang="ts">
import { ref } from 'vue'
import { search, type ChunkResult } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const query = ref('')
const collection = ref('documents')
const topK = ref(5)
const results = ref<ChunkResult[]>([])
const loading = ref(false)
const error = ref('')

async function doSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    const { data } = await search(query.value, collection.value, topK.value)
    results.value = data.results
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function scoreColor(score: number) {
  if (score >= 0.5) return 'bg-green-500/20 text-green-400'
  if (score >= 0.3) return 'bg-yellow-500/20 text-yellow-400'
  return 'bg-red-500/20 text-red-400'
}
</script>

<template>
  <div class="space-y-4">
    <!-- Search form -->
    <div class="flex gap-2">
      <input
        v-model="query"
        @keyup.enter="doSearch"
        placeholder="Nhập câu hỏi..."
        class="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-4 py-2.5 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500"
      />
      <select
        v-model="collection"
        class="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-300 focus:outline-none focus:border-violet-500"
      >
        <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
      </select>
      <select
        v-model="topK"
        class="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-slate-300 focus:outline-none focus:border-violet-500"
      >
        <option v-for="n in [3,5,10,15,20]" :key="n" :value="n">Top {{ n }}</option>
      </select>
      <button
        @click="doSearch"
        :disabled="loading"
        class="px-5 py-2.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white font-medium transition-colors"
      >
        {{ loading ? 'Đang tìm...' : 'Tìm kiếm' }}
      </button>
    </div>

    <!-- Error -->
    <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-400 text-sm">{{ error }}</div>

    <!-- Results -->
    <div v-if="results.length" class="space-y-3">
      <p class="text-slate-500 text-sm">{{ results.length }} kết quả</p>
      <div
        v-for="(r, i) in results"
        :key="i"
        class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-2"
      >
        <div class="flex items-center justify-between">
          <span class="text-violet-400 font-medium text-sm">{{ r.metadata.filename ?? 'Unknown' }}</span>
          <span :class="['text-xs font-mono px-2 py-0.5 rounded-full', scoreColor(r.score)]">
            {{ (r.score * 100).toFixed(1) }}%
          </span>
        </div>
        <div class="w-full bg-slate-700 rounded-full h-1">
          <div class="bg-violet-500 h-1 rounded-full" :style="{ width: `${r.score * 100}%` }" />
        </div>
        <pre class="text-slate-300 text-sm whitespace-pre-wrap leading-relaxed">{{ r.text }}</pre>
        <div class="flex gap-3 text-xs text-slate-500">
          <span v-if="r.metadata.section">{{ r.metadata.section }}</span>
          <span v-if="r.metadata.chunk_index">chunk #{{ r.metadata.chunk_index }}</span>
        </div>
      </div>
    </div>

    <div v-else-if="!loading" class="text-center text-slate-600 py-16">
      Nhập câu hỏi để tìm kiếm trong tài liệu
    </div>
  </div>
</template>
