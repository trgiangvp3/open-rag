<script setup lang="ts">
import { ref } from 'vue'
import { marked } from 'marked'
import { search, type ChunkResult } from '../api'
import { useCollectionsStore } from '../stores/collections'

marked.setOptions({ breaks: true, gfm: true })

function renderMd(text: string): string {
  return marked.parse(text, { async: false }) as string
}

const store = useCollectionsStore()
const query = ref('')
const collection = ref('documents')
const topK = ref(5)
const useReranker = ref(true)
const searchMode = ref<'semantic' | 'hybrid'>('hybrid')
const generate = ref(false)
const results = ref<ChunkResult[]>([])
const answer = ref<string | null>(null)
const citations = ref<number[]>([])
const loading = ref(false)
const error = ref('')

async function doSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  answer.value = null
  citations.value = []
  try {
    const { data } = await search(query.value, collection.value, topK.value, {
      useReranker: useReranker.value,
      searchMode: searchMode.value,
      generate: generate.value,
    })
    results.value = data.results
    answer.value = data.answer ?? null
    citations.value = data.citations ?? []
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

function isCited(index: number) {
  return citations.value.includes(index)
}

function renderAnswer(text: string) {
  return text.replace(/\[(\d+)\]/g, '<span class="inline-flex items-center justify-center w-5 h-5 text-xs bg-violet-600 text-white rounded-full font-bold">$1</span>')
}
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Filters & search -->
    <aside class="w-72 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Tìm kiếm</h3>

      <!-- Query -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Câu hỏi</label>
        <textarea
          v-model="query"
          @keydown.enter.exact.prevent="doSearch"
          placeholder="Nhập câu hỏi..."
          rows="3"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500 text-sm resize-none"
        />
      </div>

      <!-- Collection -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Collection</label>
        <select
          v-model="collection"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 text-sm focus:outline-none focus:border-violet-500"
        >
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
      </div>

      <!-- Top K -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Số kết quả</label>
        <select
          v-model="topK"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 text-sm focus:outline-none focus:border-violet-500"
        >
          <option v-for="n in [3,5,10,15,20]" :key="n" :value="n">Top {{ n }}</option>
        </select>
      </div>

      <!-- Search mode -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Chế độ tìm kiếm</label>
        <select
          v-model="searchMode"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 text-sm focus:outline-none focus:border-violet-500"
        >
          <option value="semantic">Semantic</option>
          <option value="hybrid">Hybrid (BM25 + Semantic)</option>
        </select>
      </div>

      <!-- Toggles -->
      <div class="space-y-2">
        <label class="flex items-center gap-2 cursor-pointer select-none text-sm text-slate-400">
          <input type="checkbox" v-model="useReranker" class="accent-violet-500" />
          Dùng Reranker
        </label>
        <label class="flex items-center gap-2 cursor-pointer select-none text-sm text-slate-400">
          <input type="checkbox" v-model="generate" class="accent-violet-500" />
          Tạo câu trả lời (RAG)
        </label>
      </div>

      <!-- Search button -->
      <button
        @click="doSearch"
        :disabled="loading || !query.trim()"
        class="w-full py-2.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white font-medium transition-colors text-sm"
      >
        {{ loading ? 'Đang tìm...' : 'Tìm kiếm' }}
      </button>
    </aside>

    <!-- Right: Results -->
    <div class="flex-1 overflow-y-auto space-y-4 min-w-0">

      <!-- Error -->
      <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-400 text-sm">{{ error }}</div>

      <!-- Generated Answer -->
      <div v-if="answer" class="bg-violet-900/20 border border-violet-700 rounded-xl p-4 space-y-2">
        <div class="flex items-center gap-2 text-violet-400 font-semibold text-sm">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.347.347a3.5 3.5 0 01-4.95 0l-.347-.347z" />
          </svg>
          Câu trả lời tổng hợp
        </div>
        <div class="text-slate-200 text-sm leading-relaxed prose prose-invert prose-sm max-w-none" v-html="renderAnswer(answer)" />
        <div v-if="citations.length" class="text-xs text-slate-500">
          Nguồn tham khảo: chunk #{{ citations.map(i => i + 1).join(', #') }}
        </div>
      </div>

      <!-- Results -->
      <div v-if="results.length" class="space-y-3">
        <p class="text-slate-500 text-sm">{{ results.length }} kết quả</p>
        <div
          v-for="(r, i) in results"
          :key="i"
          :id="`chunk-${i}`"
          :class="['bg-slate-800 border rounded-xl p-4 space-y-2', isCited(i) ? 'border-violet-500/60' : 'border-slate-700']"
        >
          <div class="flex items-center justify-between">
            <span class="text-violet-400 font-medium text-sm">
              {{ r.metadata.filename ?? 'Unknown' }}
              <span v-if="isCited(i)" class="ml-2 text-xs bg-violet-600/30 text-violet-300 px-1.5 py-0.5 rounded">trích dẫn</span>
            </span>
            <div class="flex items-center gap-2">
              <span v-if="r.rerankScore != null" class="text-xs font-mono px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">
                ↑ {{ (r.rerankScore * 100).toFixed(1) }}%
              </span>
              <span :class="['text-xs font-mono px-2 py-0.5 rounded-full', scoreColor(r.score)]">
                {{ (r.score * 100).toFixed(1) }}%
              </span>
            </div>
          </div>
          <div class="w-full bg-slate-700 rounded-full h-1">
            <div class="bg-violet-500 h-1 rounded-full" :style="{ width: `${Math.min(r.score * 100, 100)}%` }" />
          </div>
          <div class="text-slate-300 text-sm leading-relaxed prose prose-invert prose-sm max-w-none" v-html="renderMd(r.text)" />
          <div class="flex gap-3 text-xs text-slate-500">
            <span v-if="r.metadata.section">{{ r.metadata.section }}</span>
            <span v-if="r.metadata.chunk_index">chunk #{{ r.metadata.chunk_index }}</span>
          </div>
        </div>
      </div>

      <div v-else-if="!loading" class="flex items-center justify-center text-slate-600 h-full">
        Nhập câu hỏi để tìm kiếm trong tài liệu
      </div>
    </div>

  </div>
</template>
