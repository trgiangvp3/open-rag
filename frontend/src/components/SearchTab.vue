<script setup lang="ts">
defineOptions({ name: 'SearchTab' })
import { ref, computed, onMounted } from 'vue'
import { search, getDocumentMarkdown, listDomains, type ChunkResult, type DocumentChunk, type DomainInfo } from '../api'
import { useSearchSettingsStore } from '../stores/searchSettings'
import { useCollectionsStore } from '../stores/collections'
import { useProgressHub } from '../composables/useProgressHub'
import { renderMd, badgeColor, getSection as getSectionUtil, getFilename as getFilenameUtil } from '../utils/markdown'
import SourceViewer from './SourceViewer.vue'

const settings = useSearchSettingsStore()
const collectionsStore = useCollectionsStore()
const query = ref('')
const activeDomainSlug = ref('')
const domains = ref<DomainInfo[]>([])

const activeDomainChildren = computed(() => {
  if (!activeDomainSlug.value) return []
  const parent = domains.value.find(d => d.slug === activeDomainSlug.value)
  return parent?.children ?? []
})

// SignalR
const loading = ref(false)
const statusText = ref('')
const { connect } = useProgressHub('search-status', (event: { status: string }) => {
  if (loading.value) statusText.value = event.status
})

onMounted(async () => {
  connect()
  try {
    domains.value = (await listDomains()).data.domains
  } catch (e) {
    console.warn('Failed to load domains:', e)
  }
})

const results = ref<ChunkResult[]>([])
const answer = ref<string | null>(null)
const citations = ref<number[]>([])
const error = ref('')

const hasResults = computed(() => results.value.length > 0 || answer.value !== null)

// Source viewer
const viewerOpen = ref(false)
const viewerTitle = ref('')
const viewerChunks = ref<DocumentChunk[]>([])
const viewerLoading = ref(false)
const activeSourceIdx = ref<number | null>(null)
const highlightChunkId = ref<string | null>(null)
const fullMarkdown = ref<string | null>(null)

async function doSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  statusText.value = 'Đang xử lý...'
  answer.value = null
  citations.value = []
  await connect()
  try {
    const { data } = await search(query.value, settings.collection, settings.topK, {
      useReranker: settings.useReranker,
      searchMode: settings.retrievalMode,
      generate: true,
      ...(activeDomainSlug.value && { domainSlug: activeDomainSlug.value }),
      ...(settings.scoreThreshold != null && { scoreThreshold: settings.scoreThreshold }),
    })
    results.value = data.results
    answer.value = data.answer ?? null
    citations.value = data.citations ?? []
  } catch (e: unknown) {
    error.value = (e as { message?: string })?.message ?? 'Lỗi tìm kiếm'
  } finally {
    loading.value = false
    statusText.value = ''
  }
}

function selectDomain(slug: string) {
  if (slug === activeDomainSlug.value) return
  activeDomainSlug.value = slug
  if (hasResults.value || query.value.trim()) doSearch()
}

function scoreColor(score: number) {
  if (score >= 0.5) return 'score-good'
  if (score >= 0.3) return 'score-mid'
  return 'score-low'
}

function isCited(index: number) {
  return citations.value.includes(index)
}

function getSection(r: ChunkResult): string { return getSectionUtil(r.metadata) }
function getFilename(r: ChunkResult): string { return getFilenameUtil(r.metadata) }

/** Display name: "Thông tư 72/2025/TT-NHNN" or fallback to filename */
function getDocName(r: ChunkResult): string {
  const m = r.metadata
  const typeDisplay = m?.document_type_display as string | undefined
  const number = m?.document_number as string | undefined
  if (typeDisplay && number) return `${typeDisplay} ${number}`
  if (number) return number
  return getFilename(r)
}

/** Full doc label: "Thông tư 13/2018/TT-NHNN - Quy định về xxx" */
function getDocLabel(r: ChunkResult): string {
  const name = getDocName(r)
  const title = (r.metadata?.document_title as string | undefined)?.replace(/\n/g, ' ').trim()
  if (title) return `${name} - ${title}`
  return name
}

function scoreTooltip(score: number, isRerank: boolean): string {
  const pct = (score * 100).toFixed(1)
  if (isRerank) return `Rerank score: ${pct}% — Điểm xếp hạng lại bằng cross-encoder (cao = liên quan hơn)`
  if (score >= 0.5) return `Relevance: ${pct}% — Rất liên quan với truy vấn`
  if (score >= 0.3) return `Relevance: ${pct}% — Có liên quan`
  return `Relevance: ${pct}% — Ít liên quan, có thể không chính xác`
}

function openSource(chunk: ChunkResult, idx: number) {
  viewerOpen.value = true
  fullMarkdown.value = null
  viewerTitle.value = getDocName(chunk) || 'Nguồn'
  activeSourceIdx.value = idx
  viewerChunks.value = [{
    id: `source-${idx}`,
    text: chunk.text,
    metadata: chunk.metadata as Record<string, string>,
  }]
  highlightChunkId.value = `source-${idx}`
  viewerLoading.value = false
}

async function loadFullDocument(chunk: DocumentChunk) {
  const docId = chunk.metadata?.document_id
  if (!docId) return
  viewerLoading.value = true
  try {
    const { data } = await getDocumentMarkdown(docId)
    fullMarkdown.value = data.markdown
  } catch {
    fullMarkdown.value = null
  } finally {
    viewerLoading.value = false
  }
}

function closeViewer() {
  viewerOpen.value = false
  activeSourceIdx.value = null
  highlightChunkId.value = null
  fullMarkdown.value = null
}

function goHome() {
  results.value = []
  answer.value = null
  citations.value = []
  query.value = ''
  activeDomainSlug.value = ''
  viewerOpen.value = false
}
</script>

<template>
  <div class="flex h-[calc(100vh-3rem)]">

    <!-- Main content area -->
    <div class="flex-1 flex flex-col min-w-0">

      <!-- STATE A: Landing (no results, not loading) -->
      <div v-if="!hasResults && !loading" class="flex-1 flex flex-col items-center justify-center px-4">

        <div class="mb-8 text-center">
          <h1 class="text-4xl font-semibold th-accent mb-1" style="letter-spacing: -0.02em">OpenRAG</h1>
          <p class="th-text3 text-sm">Tra cứu văn bản pháp luật thông minh</p>
        </div>

        <div class="w-full max-w-2xl">
          <div class="flex items-center rounded-lg border px-1 th-border transition-all"
            style="background: var(--bg-input); box-shadow: var(--shadow-sm)"
            :style="{ borderColor: 'var(--border-primary)' }">
            <div class="flex items-center pl-3 th-text3">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <input v-model="query" @keyup.enter="doSearch"
              placeholder="Nhập câu hỏi tìm kiếm..."
              class="flex-1 bg-transparent px-3 py-3 th-text placeholder:th-text3 focus:outline-none text-sm"
              style="color: var(--text-primary)" />
            <button @click="doSearch" :disabled="!query.trim()"
              class="px-5 py-2 rounded-md text-sm font-medium transition-all mr-1 th-btn">
              Tìm kiếm
            </button>
          </div>
        </div>

        <!-- Collection selector -->
        <div v-if="collectionsStore.collections.length > 1" class="mt-4 flex items-center justify-center gap-2 text-xs">
          <span class="th-text3">Nguồn:</span>
          <select v-model="settings.collection"
            class="rounded-md px-3 py-1.5 text-xs border th-border font-medium"
            style="background: var(--bg-elevated); color: var(--text-primary)">
            <option v-for="c in collectionsStore.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
          </select>
        </div>

        <!-- Domain pills -->
        <div class="mt-4 flex flex-wrap justify-center gap-2 max-w-2xl">
          <button @click="activeDomainSlug = ''"
            :class="activeDomainSlug === '' ? 'pill-active' : 'pill'"
            class="px-4 py-1.5 rounded-md text-xs font-medium transition-all border">
            Tất cả
          </button>
          <button v-for="d in domains" :key="d.slug"
            @click="activeDomainSlug = activeDomainSlug === d.slug ? '' : d.slug"
            :class="activeDomainSlug === d.slug ? 'pill-active' : 'pill'"
            class="px-4 py-1.5 rounded-md text-xs font-medium transition-all border">
            {{ d.name }}
          </button>
        </div>

        <div v-if="activeDomainChildren.length" class="mt-2 flex flex-wrap justify-center gap-1.5 max-w-2xl">
          <button v-for="c in activeDomainChildren" :key="c.slug"
            @click="activeDomainSlug = c.slug"
            :class="activeDomainSlug === c.slug ? 'pill-active' : 'pill'"
            class="px-3 py-1 rounded-md text-[11px] font-medium transition-all border">
            {{ c.name }}
          </button>
        </div>

        <div v-if="error" class="mt-6 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 text-red-600 dark:text-red-400 text-sm max-w-2xl w-full">{{ error }}</div>
      </div>

      <!-- STATE B: Results view -->
      <template v-else>
        <!-- Top search bar -->
        <div class="flex-shrink-0 th-border border-b" style="background: var(--bg-elevated)">
          <div class="px-6 pt-3 pb-2">
            <div class="max-w-3xl flex items-center gap-3">
              <button @click="goHome" class="flex-shrink-0 th-accent font-semibold text-base hover:opacity-80 transition-opacity">OpenRAG</button>
              <select v-if="collectionsStore.collections.length > 1" v-model="settings.collection" @change="doSearch()"
                class="flex-shrink-0 rounded-md px-2 py-1 text-xs border"
                style="background: var(--bg-input); border-color: var(--border-primary); color: var(--text-secondary)">
                <option v-for="c in collectionsStore.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
              </select>
              <div class="flex-1 flex items-center rounded-md border px-1 transition-all"
                style="background: var(--bg-input); border-color: var(--border-primary)">
                <div class="flex items-center pl-2 th-text3">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </div>
                <input v-model="query" @keyup.enter="doSearch" :disabled="loading"
                  placeholder="Nhập câu hỏi tìm kiếm..."
                  class="flex-1 bg-transparent px-2 py-1.5 focus:outline-none text-sm"
                  style="color: var(--text-primary)" />
                <button @click="doSearch" :disabled="loading || !query.trim()"
                  class="px-4 py-1 rounded-md text-sm font-medium transition-all mr-0.5 disabled:opacity-30"
                  style="background: var(--accent); color: var(--accent-text)">
                  {{ loading ? 'Đang tìm...' : 'Tìm kiếm' }}
                </button>
              </div>
            </div>
          </div>

          <div class="px-6 pb-2">
            <div class="max-w-3xl flex gap-1.5 overflow-x-auto pl-[calc(4.5rem)]">
              <button @click="selectDomain('')"
                :class="activeDomainSlug === '' ? 'pill-active' : 'pill'"
                class="px-3 py-1 rounded-md text-xs font-medium transition-all border whitespace-nowrap">
                Tất cả
              </button>
              <button v-for="d in domains" :key="d.slug"
                @click="selectDomain(d.slug === activeDomainSlug ? '' : d.slug)"
                :class="(activeDomainSlug === d.slug || d.children?.some(c => c.slug === activeDomainSlug)) ? 'pill-active' : 'pill'"
                class="px-3 py-1 rounded-md text-xs font-medium transition-all border whitespace-nowrap">
                {{ d.name }}
              </button>
            </div>
            <div v-if="activeDomainChildren.length" class="max-w-3xl flex gap-1.5 overflow-x-auto mt-1.5 pl-[calc(4.5rem)]">
              <button v-for="c in activeDomainChildren" :key="c.slug"
                @click="selectDomain(c.slug)"
                :class="activeDomainSlug === c.slug ? 'pill-active' : 'pill'"
                class="px-2.5 py-0.5 rounded-md text-[11px] font-medium transition-all border whitespace-nowrap">
                {{ c.name }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="loading" class="px-6 py-3">
          <div class="max-w-3xl flex items-center gap-2 pl-[calc(4.5rem)]">
            <div class="flex gap-1">
              <span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--accent); animation-delay: 0ms" />
              <span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--accent); animation-delay: 150ms" />
              <span class="w-1.5 h-1.5 rounded-full animate-bounce" style="background: var(--accent); animation-delay: 300ms" />
            </div>
            <span class="th-text3 text-xs">{{ statusText || 'Đang xử lý...' }}</span>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto px-6 py-4">
          <div class="max-w-3xl ml-[calc(4.5rem)] space-y-4">
            <div v-if="error" class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-4 py-3 text-red-600 dark:text-red-400 text-sm">{{ error }}</div>

            <!-- AI Answer card -->
            <div v-if="answer" class="rounded-xl border p-5 th-border"
              style="background: var(--bg-elevated); box-shadow: var(--shadow-md)">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-6 h-6 rounded-md flex items-center justify-center"
                  style="background: var(--accent-light)">
                  <svg class="w-3.5 h-3.5 th-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.347.347a3.5 3.5 0 01-4.95 0l-.347-.347z" />
                  </svg>
                </div>
                <span class="th-accent font-semibold text-xs uppercase tracking-wider">Tổng hợp AI</span>
              </div>
              <div class="prose prose-sm dark:prose-invert max-w-none
                prose-p:my-2 prose-p:leading-relaxed
                prose-headings:font-semibold prose-strong:font-semibold"
                style="color: var(--text-primary)"
                v-html="renderMd(answer)" />

              <div v-if="citations.length" class="mt-4 pt-3" style="border-top: 1px solid var(--border-secondary)">
                <div class="flex flex-wrap gap-2">
                  <button v-for="ci in citations" :key="ci"
                    @click="openSource(results[ci], ci)"
                    class="flex items-center gap-1.5 px-2.5 py-1 rounded-md border th-border th-hover transition-all text-xs">
                    <span :class="['w-4 h-4 rounded-full text-[9px] text-white font-bold flex items-center justify-center', badgeColor(ci)]">{{ ci + 1 }}</span>
                    <span class="th-text2 truncate max-w-[180px]">{{ getDocName(results[ci]) }}</span>
                  </button>
                </div>
              </div>
            </div>

            <p v-if="results.length" class="th-text3 text-xs">{{ results.length }} kết quả</p>

            <!-- Result items -->
            <div v-for="(r, i) in results" :key="i"
              @click="openSource(r, i)"
              class="group cursor-pointer py-4 border-b th-border2 last:border-0">
              <!-- Source line: doc name + title + date -->
              <div class="flex items-center gap-2 mb-1.5">
                <span :class="['inline-flex items-center justify-center w-5 h-5 text-[9px] text-white rounded-full font-bold flex-shrink-0', badgeColor(i)]">{{ i + 1 }}</span>
                <p class="th-text3 text-xs truncate">
                  {{ getDocLabel(r) }}
                  <span v-if="r.metadata?.issue_date"> · {{ r.metadata.issue_date }}</span>
                </p>
              </div>
              <!-- Section heading -->
              <h3 class="th-link text-base font-medium mb-1.5 group-hover:underline underline-offset-2">
                {{ getSection(r) || getDocName(r) }}
              </h3>
              <!-- Content as markdown -->
              <div class="th-text2 text-sm leading-relaxed line-clamp-4 prose prose-sm dark:prose-invert max-w-none
                prose-p:my-0.5 prose-p:leading-relaxed prose-headings:text-sm prose-headings:font-medium
                prose-hr:my-1 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0"
                style="color: var(--text-secondary)"
                v-html="renderMd(r.text.slice(0, 500))" />
              <!-- Badges row -->
              <div class="flex items-center gap-2 mt-2">
                <span v-if="isCited(i)" class="text-[10px] px-2 py-0.5 rounded-md font-medium"
                  style="background: var(--badge-cited-bg); color: var(--badge-cited-text)">Được trích dẫn</span>
                <!-- When reranker is used: show rerank as primary, relevance as secondary -->
                <template v-if="r.rerankScore != null">
                  <span :class="['text-[10px] font-mono px-2 py-0.5 rounded-md cursor-help', scoreColor(r.rerankScore)]"
                    :title="scoreTooltip(r.rerankScore, true)">{{ (r.rerankScore * 100).toFixed(0) }}%</span>
                  <span class="text-[10px] font-mono px-2 py-0.5 rounded-md cursor-help"
                    style="color: var(--text-tertiary)"
                    :title="scoreTooltip(r.score, false)">rel {{ (r.score * 100).toFixed(0) }}%</span>
                </template>
                <!-- No reranker: show relevance as primary -->
                <span v-else :class="['text-[10px] font-mono px-2 py-0.5 rounded-md cursor-help', scoreColor(r.score)]"
                  :title="scoreTooltip(r.score, false)">{{ (r.score * 100).toFixed(0) }}%</span>
              </div>
            </div>

            <div v-if="!loading && !results.length && !answer" class="th-text3 text-sm py-8 text-center">
              Không tìm thấy kết quả phù hợp.
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Source viewer -->
    <SourceViewer
      :open="viewerOpen"
      :title="viewerTitle"
      :chunks="viewerChunks"
      :loading="viewerLoading"
      :active-idx="activeSourceIdx"
      :highlight-id="highlightChunkId"
      :full-markdown="fullMarkdown"
      @close="closeViewer"
      @load-full="loadFullDocument"
    />
  </div>
</template>

<style scoped>
.pill {
  background: var(--bg-tertiary);
  border-color: var(--border-secondary);
  color: var(--text-secondary);
}
.pill:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.pill-active {
  background: var(--accent-light);
  border-color: var(--accent);
  color: var(--text-accent);
}
.score-good {
  background: var(--score-good-bg);
  color: var(--score-good-text);
}
.score-mid {
  background: var(--score-mid-bg);
  color: var(--score-mid-text);
}
.score-low {
  background: var(--score-low-bg);
  color: var(--score-low-text);
}
</style>
