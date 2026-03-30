<script setup lang="ts">
defineOptions({ name: 'SearchTab' })
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import * as signalR from '@microsoft/signalr'
import { marked } from 'marked'
import { search, getDocumentChunks, listDomains, type ChunkResult, type DocumentChunk, type DomainInfo } from '../api'
import { useCollectionsStore } from '../stores/collections'

marked.setOptions({ breaks: true, gfm: true })

function renderMd(text: string): string {
  const html = marked.parse(text, { async: false }) as string
  return html.replace(/\[(\d+)\]/g, (_m, n) => {
    const colors = ['bg-violet-600', 'bg-blue-600', 'bg-emerald-600', 'bg-amber-600', 'bg-rose-600', 'bg-cyan-600', 'bg-pink-600', 'bg-teal-600']
    const c = colors[(parseInt(n) - 1) % colors.length]
    return `<span class="inline-flex items-center justify-center w-5 h-5 text-[10px] ${c} text-white rounded-full font-bold mx-0.5 align-middle shadow-sm">${n}</span>`
  })
}

function renderMdPlain(text: string): string {
  return marked.parse(text, { async: false }) as string
}

const store = useCollectionsStore()
const query = ref('')
const collection = ref('documents')
const topK = ref(5)
const useReranker = ref(true)
const queryStrategy = ref<'direct' | 'multi-query' | 'hyde' | 'multi-query+hyde'>('direct')
const retrievalMode = ref<'semantic' | 'hybrid'>('hybrid')
const generate = ref(false)
const filterDocumentType = ref('')
const filterDomainSlug = ref('')
const filterSubject = ref('')
const filterDateFrom = ref('')
const filterDateTo = ref('')
const domains = ref<DomainInfo[]>([])

// Flatten domains for select options
const domainOptions = computed(() => {
  const opts: { slug: string; label: string }[] = []
  for (const d of domains.value) {
    opts.push({ slug: d.slug, label: d.name })
    for (const c of d.children ?? [])
      opts.push({ slug: c.slug, label: `  ${d.name} > ${c.name}` })
  }
  return opts
})

onMounted(async () => {
  ensureHub()
  try { domains.value = (await listDomains()).data.domains } catch {}
})
const SEARCH_KEY = 'openrag_search_state'

const results = ref<ChunkResult[]>([])
const answer = ref<string | null>(null)
const citations = ref<number[]>([])
const loading = ref(false)
const error = ref('')
const statusText = ref('')

// Restore last search from localStorage
try {
  const saved = JSON.parse(localStorage.getItem(SEARCH_KEY) ?? 'null')
  if (saved) {
    query.value = saved.query ?? ''
    results.value = saved.results ?? []
    answer.value = saved.answer ?? null
    citations.value = saved.citations ?? []
  }
} catch {}

// Persist search results
function saveSearch() {
  if (!results.value.length) return
  try {
    localStorage.setItem(SEARCH_KEY, JSON.stringify({
      query: query.value, results: results.value,
      answer: answer.value, citations: citations.value,
    }))
  } catch {}
}
watch([results, answer, citations], saveSearch, { deep: true })

// Source viewer (same as ChatTab)
const viewerOpen = ref(false)
const viewerTitle = ref('')
const viewerChunks = ref<DocumentChunk[]>([])
const viewerLoading = ref(false)
const activeSourceIdx = ref<number | null>(null)
const highlightChunkId = ref<string | null>(null)

const sourceBadgeColors = ['bg-violet-600', 'bg-blue-600', 'bg-emerald-600', 'bg-amber-600', 'bg-rose-600', 'bg-cyan-600', 'bg-pink-600', 'bg-teal-600']
function badgeColor(idx: number) { return sourceBadgeColors[idx % sourceBadgeColors.length] }

// ── SignalR for search status ─────────────────────────────────────────────
let hubConnection: signalR.HubConnection | null = null

async function ensureHub() {
  if (hubConnection) return
  hubConnection = new signalR.HubConnectionBuilder()
    .withUrl('/ws/progress')
    .withAutomaticReconnect([0, 2000, 5000, 10000])
    .build()

  hubConnection.on('search-status', (event: { status: string }) => {
    if (loading.value) {
      statusText.value = event.status
    }
  })

  hubConnection.onclose(() => { hubConnection = null })
  try { await hubConnection.start() } catch { hubConnection = null }
}

// onMounted moved to domain loading block above
onUnmounted(async () => {
  if (hubConnection) { try { await hubConnection.stop() } catch {} hubConnection = null }
})

async function doSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  statusText.value = 'Đang xử lý...'
  answer.value = null
  citations.value = []
  await ensureHub()
  try {
    const { data } = await search(query.value, collection.value, topK.value, {
      useReranker: useReranker.value,
      searchMode: retrievalMode.value,
      queryStrategy: queryStrategy.value,
      generate: generate.value,
      ...(filterDocumentType.value && { documentType: filterDocumentType.value }),
      ...(filterDomainSlug.value && { domainSlug: filterDomainSlug.value }),
      ...(filterSubject.value && { subject: filterSubject.value }),
      ...(filterDateFrom.value && { dateFrom: filterDateFrom.value }),
      ...(filterDateTo.value && { dateTo: filterDateTo.value }),
    })
    results.value = data.results
    answer.value = data.answer ?? null
    citations.value = data.citations ?? []
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
    statusText.value = ''
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

function getSection(r: ChunkResult): string {
  return (r.metadata?.section ?? '') as string
}

function getFilename(r: ChunkResult): string {
  return (r.metadata?.filename ?? '') as string
}

// Source viewer
function openSource(chunk: ChunkResult, idx: number) {
  viewerOpen.value = true
  viewerTitle.value = getFilename(chunk) || 'Nguồn'
  activeSourceIdx.value = idx
  viewerChunks.value = [{
    id: `source-${idx}`,
    text: chunk.text,
    metadata: chunk.metadata as Record<string, string>,
  }]
  highlightChunkId.value = `source-${idx}`
  viewerLoading.value = false
}

async function loadFullDocument(chunk: any) {
  const docId = chunk.metadata?.document_id
  if (!docId) return
  viewerLoading.value = true
  try {
    const { data } = await getDocumentChunks(docId, collection.value)
    viewerChunks.value = data.chunks
    await nextTick()
    const snippet = chunk.text.slice(0, 100)
    const match = viewerChunks.value.find((c: DocumentChunk) => c.text === chunk.text)
      ?? viewerChunks.value.find((c: DocumentChunk) => c.text.includes(snippet))
    if (match) {
      highlightChunkId.value = match.id
      await nextTick()
      document.getElementById(`viewer-chunk-${match.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  } catch {
    viewerChunks.value = []
  } finally {
    viewerLoading.value = false
  }
}
</script>

<template>
  <div class="flex gap-0 h-[calc(100vh-8rem)]">

    <!-- Col 1: Settings sidebar (same style as ChatTab) -->
    <aside class="w-48 flex-shrink-0 border-r border-slate-700/50 bg-slate-900/50 p-3 space-y-4">
      <div class="flex items-center gap-2">
        <div class="w-2 h-2 rounded-full bg-emerald-500" />
        <h3 class="text-slate-300 text-xs font-semibold uppercase tracking-widest">Tìm kiếm</h3>
      </div>

      <div class="space-y-1">
        <label class="text-slate-500 text-[10px] uppercase tracking-wider">Collection</label>
        <select v-model="collection"
          class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
      </div>

      <div class="space-y-1">
        <label class="text-slate-500 text-[10px] uppercase tracking-wider">Số kết quả</label>
        <select v-model="topK"
          class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option v-for="n in [3,5,10,15,20]" :key="n" :value="n">Top {{ n }}</option>
        </select>
      </div>

      <div class="space-y-1">
        <label class="text-slate-500 text-[10px] uppercase tracking-wider">Query</label>
        <select v-model="queryStrategy"
          class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option value="direct">Trực tiếp</option>
          <option value="multi-query">Multi-query</option>
          <option value="hyde">HyDE</option>
          <option value="multi-query+hyde">Multi + HyDE</option>
        </select>
      </div>

      <div class="space-y-1">
        <label class="text-slate-500 text-[10px] uppercase tracking-wider">Phương pháp</label>
        <select v-model="retrievalMode"
          class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option value="semantic">Semantic</option>
          <option value="hybrid">Hybrid</option>
        </select>
      </div>

      <label class="flex items-center gap-2 cursor-pointer select-none text-xs text-slate-400">
        <input type="checkbox" v-model="useReranker" class="accent-violet-500 rounded" />
        Reranker
      </label>

      <label class="flex items-center gap-2 cursor-pointer select-none text-xs text-slate-400">
        <input type="checkbox" v-model="generate" class="accent-violet-500 rounded" />
        Tạo câu trả lời (RAG)
      </label>

      <!-- Facet filters -->
      <div class="border-t border-slate-700/50 pt-3 mt-1">
        <p class="text-slate-500 text-[10px] uppercase tracking-wider mb-2">Bộ lọc</p>

        <div class="space-y-1">
          <label class="text-slate-500 text-[10px] uppercase tracking-wider">Lĩnh vực</label>
          <select v-model="filterDomainSlug"
            class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
            <option value="">Tất cả</option>
            <option v-for="d in domainOptions" :key="d.slug" :value="d.slug">{{ d.label }}</option>
          </select>
        </div>

        <div class="space-y-1 mt-2">
          <label class="text-slate-500 text-[10px] uppercase tracking-wider">Đối tượng áp dụng</label>
          <input v-model="filterSubject" type="text" placeholder="VD: ngân hàng thương mại"
            class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500 placeholder-slate-600" />
        </div>

        <div class="space-y-1 mt-2">
          <label class="text-slate-500 text-[10px] uppercase tracking-wider">Loại văn bản</label>
          <select v-model="filterDocumentType"
            class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
            <option value="">Tất cả</option>
            <option value="luat">Luật</option>
            <option value="nghi_dinh">Nghị định</option>
            <option value="thong_tu">Thông tư</option>
            <option value="quyet_dinh">Quyết định</option>
            <option value="nghi_quyet">Nghị quyết</option>
            <option value="chi_thi">Chỉ thị</option>
            <option value="cong_van">Công văn</option>
          </select>
        </div>

        <div class="grid grid-cols-2 gap-2 mt-2">
          <div class="space-y-1">
            <label class="text-slate-500 text-[10px] uppercase tracking-wider">Từ ngày</label>
            <input v-model="filterDateFrom" type="date"
              class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500" />
          </div>
          <div class="space-y-1">
            <label class="text-slate-500 text-[10px] uppercase tracking-wider">Đến ngày</label>
            <input v-model="filterDateTo" type="date"
              class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500" />
          </div>
        </div>
      </div>
    </aside>

    <!-- Col 2: Query + Results -->
    <div :class="['flex-1 flex flex-col min-w-0 transition-all', viewerOpen ? 'border-r border-slate-700/50' : '']">

      <!-- Query bar -->
      <div class="px-6 pt-4 pb-3">
        <div class="flex gap-2 bg-slate-800/50 border border-slate-700/50 rounded-2xl p-1.5 shadow-lg focus-within:border-violet-500/50 transition-all">
          <input v-model="query" @keyup.enter="doSearch" :disabled="loading"
            placeholder="Nhập câu hỏi tìm kiếm..."
            class="flex-1 bg-transparent px-4 py-2 text-slate-100 placeholder-slate-500 focus:outline-none disabled:opacity-50 text-sm" />
          <button @click="doSearch" :disabled="loading || !query.trim()"
            class="px-5 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-30 rounded-xl text-white text-sm font-medium transition-all shadow-md shadow-violet-900/30">
            {{ loading ? 'Đang tìm...' : 'Tìm kiếm' }}
          </button>
        </div>
      </div>

      <!-- Status bar -->
      <div v-if="loading" class="px-6 pb-2">
        <div class="flex items-center gap-2 bg-slate-800/60 border border-slate-700/50 rounded-xl px-4 py-2">
          <div class="flex gap-1">
            <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 0ms" />
            <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 150ms" />
            <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 300ms" />
          </div>
          <span class="text-slate-400 text-xs">{{ statusText || 'Đang xử lý...' }}</span>
        </div>
      </div>

      <!-- Results -->
      <div class="flex-1 overflow-y-auto px-6 pb-4 space-y-4">

        <!-- Error -->
        <div v-if="error" class="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-400 text-sm">{{ error }}</div>

        <!-- Generated Answer -->
        <div v-if="answer" class="bg-gradient-to-br from-slate-800/80 to-slate-800/40 border border-slate-700/50 rounded-2xl px-6 py-4 shadow-lg">
          <div class="flex items-center gap-2 text-violet-400 font-semibold text-xs uppercase tracking-wider mb-3">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.347.347a3.5 3.5 0 01-4.95 0l-.347-.347z" />
            </svg>
            Câu trả lời tổng hợp
          </div>
          <div class="prose prose-invert prose-sm max-w-none
            prose-p:my-2 prose-p:leading-relaxed prose-p:text-slate-300
            prose-headings:text-slate-100 prose-strong:text-white prose-em:text-violet-300
            prose-code:text-violet-300 prose-code:bg-slate-700/50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs"
            v-html="renderMd(answer)" />

          <!-- Reference table (same as ChatTab) -->
          <div v-if="citations.length" class="mt-4 pt-3 border-t border-slate-700/30">
            <p class="text-slate-500 text-[10px] uppercase tracking-wider mb-2">Tham chiếu</p>
            <div class="space-y-1">
              <div v-for="ci in citations" :key="ci"
                class="flex items-start gap-2 py-1.5 hover:bg-slate-700/20 cursor-pointer transition-colors rounded px-1"
                @click="openSource(results[ci], ci)">
                <span :class="['inline-flex items-center justify-center w-5 h-5 text-[10px] text-white rounded-full font-bold flex-shrink-0 mt-0.5', badgeColor(ci)]">{{ ci + 1 }}</span>
                <div class="min-w-0">
                  <p v-if="getSection(results[ci])" class="text-slate-200 text-xs font-medium">{{ getSection(results[ci]) }}</p>
                  <p class="text-slate-500 text-[11px]">{{ getFilename(results[ci]) }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Results list -->
        <div v-if="results.length" class="space-y-3">
          <p class="text-slate-500 text-xs">{{ results.length }} kết quả</p>
          <div
            v-for="(r, i) in results"
            :key="i"
            @click="openSource(r, i)"
            :class="['bg-slate-800/60 border rounded-xl p-4 space-y-2 cursor-pointer transition-all hover:border-slate-600',
              isCited(i) ? 'border-violet-500/40' : 'border-slate-700/50']"
          >
            <!-- Header -->
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2 min-w-0">
                <span :class="['inline-flex items-center justify-center w-5 h-5 text-[10px] text-white rounded-full font-bold shadow-sm', badgeColor(i)]">{{ i + 1 }}</span>
                <span v-if="getSection(r)" class="text-slate-200 text-sm font-medium truncate">{{ getSection(r) }}</span>
                <span class="text-slate-500 text-xs truncate">{{ getFilename(r) }}</span>
              </div>
              <div class="flex items-center gap-2 flex-shrink-0">
                <span v-if="r.rerankScore != null" class="text-xs font-mono px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">
                  ↑ {{ (r.rerankScore * 100).toFixed(0) }}%
                </span>
                <span :class="['text-xs font-mono px-2 py-0.5 rounded-full', scoreColor(r.score)]">
                  {{ (r.score * 100).toFixed(0) }}%
                </span>
              </div>
            </div>

            <!-- Score bar -->
            <div class="w-full bg-slate-700/50 rounded-full h-0.5">
              <div class="bg-violet-500/60 h-0.5 rounded-full" :style="{ width: `${Math.min(r.score * 100, 100)}%` }" />
            </div>

            <!-- Content (rendered markdown) -->
            <div class="prose prose-invert prose-sm max-w-none
              prose-p:text-slate-300 prose-p:my-1.5 prose-p:leading-relaxed prose-p:text-sm
              prose-headings:text-slate-100 prose-strong:text-white
              prose-code:text-violet-300 prose-code:bg-slate-700/50 prose-code:px-1 prose-code:rounded prose-code:text-xs"
              v-html="renderMdPlain(r.text)" />
          </div>
        </div>

        <div v-else-if="!loading && !answer" class="flex items-center justify-center text-slate-600 h-full">
          <p class="text-sm">Nhập câu hỏi để tìm kiếm trong tài liệu</p>
        </div>
      </div>
    </div>

    <!-- Col 3: Source viewer (same as ChatTab) -->
    <div v-if="viewerOpen" class="w-[38%] flex-shrink-0 flex flex-col min-w-0 bg-slate-900/30">
      <div class="p-3 border-b border-slate-700/50 flex items-center justify-between bg-slate-800/30">
        <div class="flex items-center gap-2 min-w-0">
          <span v-if="activeSourceIdx !== null" :class="['inline-flex items-center justify-center w-5 h-5 text-[10px] text-white rounded-full font-bold shadow-sm', badgeColor(activeSourceIdx)]">{{ activeSourceIdx + 1 }}</span>
          <div class="min-w-0">
            <p class="text-slate-200 text-sm font-medium truncate">{{ viewerTitle }}</p>
            <p v-if="viewerChunks.length === 1 && viewerChunks[0].metadata?.section" class="text-slate-400 text-xs truncate">{{ viewerChunks[0].metadata.section }}</p>
          </div>
        </div>
        <div class="flex items-center gap-1.5 flex-shrink-0">
          <button v-if="viewerChunks.length === 1 && viewerChunks[0].metadata?.document_id"
            @click="loadFullDocument(viewerChunks[0])"
            class="px-2 py-1 text-[10px] text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-600/50 rounded transition-all">
            Xem toàn văn
          </button>
          <button @click="viewerOpen = false; activeSourceIdx = null; highlightChunkId = null"
            class="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-200 hover:bg-slate-700/50 transition-all">&times;</button>
        </div>
      </div>

      <div v-if="viewerLoading" class="flex-1 flex items-center justify-center">
        <div class="flex items-center gap-2 text-slate-500 text-sm">
          <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Đang tải...
        </div>
      </div>
      <div v-else class="flex-1 overflow-y-auto">
        <div v-for="(chunk, i) in viewerChunks" :key="chunk.id"
          :id="`viewer-chunk-${chunk.id}`"
          :class="['border-b border-slate-800/50 transition-all', highlightChunkId === chunk.id ? 'bg-violet-900/15 border-l-2 border-l-violet-500' : '']">
          <div v-if="viewerChunks.length > 1" class="px-4 py-1.5 bg-slate-800/30 flex items-center gap-2">
            <span class="text-violet-400/70 text-[10px] font-mono">#{{ i }}</span>
            <span v-if="chunk.metadata?.section" class="text-slate-500 text-xs truncate">{{ chunk.metadata.section }}</span>
          </div>
          <div class="px-5 py-4">
            <div class="prose prose-invert prose-sm max-w-none
              prose-p:text-slate-300 prose-p:my-2 prose-p:leading-relaxed
              prose-headings:text-slate-100 prose-strong:text-white
              prose-code:text-violet-300 prose-code:bg-slate-700/50 prose-code:px-1 prose-code:rounded"
              v-html="renderMdPlain(chunk.text)" />
          </div>
        </div>
      </div>
    </div>

  </div>
</template>
