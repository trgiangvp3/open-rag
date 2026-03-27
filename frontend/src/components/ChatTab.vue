<script setup lang="ts">
defineOptions({ name: 'ChatTab' })
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import * as signalR from '@microsoft/signalr'
import { marked } from 'marked'
import { chat, getChatHistory, deleteChatSession, getDocumentChunks, type ChunkResult, type DocumentChunk } from '../api'
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
const collection = ref('documents')
const useReranker = ref(true)
const queryStrategy = ref<'direct' | 'multi-query' | 'hyde' | 'multi-query+hyde'>('direct')
const retrievalMode = ref('hybrid')

const SESSION_KEY = 'openrag_chat_session_id'
const MESSAGES_KEY = 'openrag_chat_messages'

interface Message {
  role: 'user' | 'assistant'
  content: string
  chunks?: ChunkResult[]
  citations?: number[]
}

const sessionId = ref<string | null>(localStorage.getItem(SESSION_KEY))
const messages = ref<Message[]>([])
const query = ref('')
const loading = ref(false)
const statusText = ref('')
const error = ref('')
const messagesEl = ref<HTMLElement | null>(null)

// Viewer
const viewerOpen = ref(false)
const viewerTitle = ref('')
const viewerChunks = ref<DocumentChunk[]>([])
const viewerLoading = ref(false)
const viewerDocId = ref<string | null>(null)
const activeSourceIdx = ref<number | null>(null)
const highlightChunkId = ref<string | null>(null)

const sourceColors = ['border-violet-500/60 bg-violet-500/10', 'border-blue-500/60 bg-blue-500/10', 'border-emerald-500/60 bg-emerald-500/10', 'border-amber-500/60 bg-amber-500/10', 'border-rose-500/60 bg-rose-500/10', 'border-cyan-500/60 bg-cyan-500/10', 'border-pink-500/60 bg-pink-500/10', 'border-teal-500/60 bg-teal-500/10']
const sourceBadgeColors = ['bg-violet-600', 'bg-blue-600', 'bg-emerald-600', 'bg-amber-600', 'bg-rose-600', 'bg-cyan-600', 'bg-pink-600', 'bg-teal-600']

function sourceColor(idx: number) { return sourceColors[idx % sourceColors.length] }
function badgeColor(idx: number) { return sourceBadgeColors[idx % sourceBadgeColors.length] }

// ── SignalR for chat status ──────────────────────────────────────────────────
let hubConnection: signalR.HubConnection | null = null

async function ensureHub() {
  if (hubConnection) return
  hubConnection = new signalR.HubConnectionBuilder()
    .withUrl('/ws/progress')
    .withAutomaticReconnect([0, 2000, 5000, 10000])
    .build()

  hubConnection.on('chat-status', (event: { sessionId: string; status: string }) => {
    // Accept status if loading — on first message, sessionId may not be set yet
    if (loading.value) {
      statusText.value = event.status
    }
  })

  hubConnection.onclose(() => { hubConnection = null })

  try { await hubConnection.start() } catch { hubConnection = null }
}

// Persist messages (with chunks/citations) to localStorage
function saveMessages() {
  try { localStorage.setItem(MESSAGES_KEY, JSON.stringify(messages.value)) } catch {}
}
watch(messages, saveMessages, { deep: true })

onMounted(() => {
  ensureHub()
  // Load from localStorage first (includes chunks/citations)
  const saved = localStorage.getItem(MESSAGES_KEY)
  if (saved) {
    try { messages.value = JSON.parse(saved) } catch {}
  }
  // If no local data but session exists, fallback to API (text only)
  if (!messages.value.length && sessionId.value) {
    getChatHistory(sessionId.value).then(({ data }) => {
      messages.value = data.messages.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))
    }).catch(() => {
      sessionId.value = null
      localStorage.removeItem(SESSION_KEY)
    })
  }
})

onUnmounted(async () => {
  if (hubConnection) { try { await hubConnection.stop() } catch {} hubConnection = null }
})

// ── Chat logic ───────────────────────────────────────────────────────────────

async function sendMessage() {
  if (!query.value.trim() || loading.value) return
  const userQuery = query.value
  query.value = ''
  messages.value.push({ role: 'user', content: userQuery })
  loading.value = true
  statusText.value = 'Đang xử lý...'
  error.value = ''
  await ensureHub()
  await scrollToBottom()

  try {
    const { data } = await chat({
      query: userQuery,
      collection: collection.value,
      sessionId: sessionId.value ?? undefined,
      topK: 5,
      useReranker: useReranker.value,
      searchMode: retrievalMode.value,
      queryStrategy: queryStrategy.value,
    })
    sessionId.value = data.sessionId
    localStorage.setItem(SESSION_KEY, data.sessionId)
    messages.value.push({
      role: 'assistant',
      content: data.answer ?? '*(LLM chưa được cấu hình — vào **Cài đặt** để thiết lập)*',
      chunks: data.chunks,
      citations: data.citations ?? [],
    })
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? e.message
    messages.value.push({ role: 'assistant', content: `Lỗi: ${error.value}` })
  } finally {
    loading.value = false
    statusText.value = ''
    await scrollToBottom()
  }
}

function newConversation() {
  if (sessionId.value) deleteChatSession(sessionId.value).catch(() => {})
  sessionId.value = null
  localStorage.removeItem(SESSION_KEY)
  localStorage.removeItem(MESSAGES_KEY)
  messages.value = []
  error.value = ''
  viewerOpen.value = false
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

function isCited(msg: Message, idx: number) {
  return msg.citations?.includes(idx) ?? false
}

// ── Source viewer ────────────────────────────────────────────────────────────

// Show only the cited chunk directly — no need to load all document chunks
function openSource(chunk: ChunkResult, idx: number) {
  viewerOpen.value = true
  viewerTitle.value = chunk.metadata.filename ?? 'Nguồn'
  activeSourceIdx.value = idx
  // Show just this one chunk in the viewer
  viewerChunks.value = [{
    id: `source-${idx}`,
    text: chunk.text,
    metadata: chunk.metadata as Record<string, string>,
  }]
  highlightChunkId.value = `source-${idx}`
  viewerLoading.value = false
}

// Load full document chunks when user wants to see context
async function loadFullDocument(chunk: ChunkResult) {
  const docId = (chunk.metadata.document_id ?? '') as string
  if (!docId) return

  viewerDocId.value = docId
  viewerLoading.value = true
  try {
    const { data } = await getDocumentChunks(docId, collection.value)
    viewerChunks.value = data.chunks
    // Find and highlight the matching chunk
    await nextTick()
    const snippet = chunk.text.slice(0, 100)
    const match = viewerChunks.value.find(c => c.text === chunk.text)
      ?? viewerChunks.value.find(c => c.text.includes(snippet))
      ?? viewerChunks.value.find(c => chunk.text.includes(c.text.slice(0, 100)))
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

// ── Reference helpers ────────────────────────────────────────────────────────

function getSection(chunk: ChunkResult): string {
  return (chunk.metadata?.section ?? '') as string
}

function getFilename(chunk: ChunkResult): string {
  return (chunk.metadata?.filename ?? '') as string
}
</script>

<template>
  <div class="flex gap-0 h-[calc(100vh-8rem)]">

    <!-- Col 1: Settings sidebar -->
    <aside class="w-48 flex-shrink-0 border-r border-slate-700/50 bg-slate-900/50 p-3 space-y-4">
      <div class="flex items-center gap-2">
        <div class="w-2 h-2 rounded-full bg-violet-500 animate-pulse" />
        <h3 class="text-slate-300 text-xs font-semibold uppercase tracking-widest">Chat</h3>
      </div>

      <div class="space-y-1">
        <label class="text-slate-500 text-[10px] uppercase tracking-wider">Collection</label>
        <select v-model="collection"
          class="w-full bg-slate-800/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
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

      <div class="pt-2 border-t border-slate-700/50">
        <button @click="newConversation"
          class="w-full py-2 bg-slate-800/80 hover:bg-slate-700/80 border border-slate-600/30 rounded-lg text-slate-400 hover:text-slate-200 text-xs transition-all">
          + Hội thoại mới
        </button>
      </div>
    </aside>

    <!-- Col 2: Chat area -->
    <div :class="['flex-1 flex flex-col min-w-0 transition-all', viewerOpen ? 'border-r border-slate-700/50' : '']">

      <!-- Messages -->
      <div ref="messagesEl" class="flex-1 overflow-y-auto px-6 py-5 space-y-6">
        <!-- Empty state -->
        <div v-if="!messages.length" class="flex flex-col items-center justify-center h-full text-slate-600 space-y-3">
          <div class="w-16 h-16 rounded-2xl bg-slate-800/50 border border-slate-700/50 flex items-center justify-center">
            <svg class="w-8 h-8 text-violet-500/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
            </svg>
          </div>
          <p class="text-sm">Bắt đầu hội thoại với tài liệu của bạn</p>
        </div>

        <template v-for="(msg, i) in messages" :key="i">
          <!-- User bubble -->
          <div v-if="msg.role === 'user'" class="flex justify-end">
            <div class="max-w-[75%] bg-gradient-to-br from-violet-700/50 to-violet-800/50 border border-violet-600/20 rounded-2xl rounded-tr-md px-5 py-3 shadow-lg shadow-violet-900/10">
              <p class="text-slate-100 text-sm leading-relaxed">{{ msg.content }}</p>
            </div>
          </div>

          <!-- Assistant -->
          <div v-else class="space-y-3">
            <!-- Answer -->
            <div class="bg-gradient-to-br from-slate-800/80 to-slate-800/40 border border-slate-700/50 rounded-2xl rounded-tl-md px-6 py-4 shadow-lg shadow-slate-900/20">
              <div class="prose prose-invert prose-sm max-w-none
                prose-p:my-2 prose-p:leading-relaxed prose-p:text-slate-300
                prose-li:my-0.5 prose-li:text-slate-300 prose-ul:my-2 prose-ol:my-2
                prose-headings:mt-4 prose-headings:mb-2 prose-headings:text-slate-100
                prose-strong:text-white prose-em:text-violet-300
                prose-code:text-violet-300 prose-code:bg-slate-700/50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                prose-blockquote:border-violet-500/50 prose-blockquote:bg-slate-800/30 prose-blockquote:rounded-r-lg
                prose-a:text-violet-400"
                v-html="renderMd(msg.content)" />
            </div>

            <!-- Reference table -->
            <div v-if="msg.chunks?.length && msg.citations?.length" class="bg-slate-800/30 border border-slate-700/30 rounded-xl px-4 py-3">
              <p class="text-slate-500 text-[10px] uppercase tracking-wider mb-2">Tham chiếu</p>
              <div class="space-y-1">
                <div v-for="ci in msg.citations" :key="ci"
                  class="flex items-start gap-2 py-1.5 border-b border-slate-700/20 last:border-0 hover:bg-slate-700/20 cursor-pointer transition-colors rounded px-1"
                  @click="openSource(msg.chunks![ci], ci)">
                  <span :class="['inline-flex items-center justify-center w-5 h-5 text-[10px] text-white rounded-full font-bold flex-shrink-0 mt-0.5', badgeColor(ci)]">{{ ci + 1 }}</span>
                  <div class="min-w-0">
                    <p v-if="getSection(msg.chunks![ci])" class="text-slate-200 text-xs font-medium">{{ getSection(msg.chunks![ci]) }}</p>
                    <p class="text-slate-500 text-[11px]">{{ getFilename(msg.chunks![ci]) }}</p>
                    <p v-if="!getSection(msg.chunks![ci])" class="text-slate-400 text-[11px] truncate">{{ msg.chunks![ci].text.slice(0, 80) }}...</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- Source pills -->
            <div v-if="msg.chunks?.length" class="flex items-start gap-2 pl-1">
              <span class="text-slate-600 text-[10px] uppercase tracking-wider mt-1.5 flex-shrink-0">Nguồn</span>
              <div class="flex flex-wrap gap-1.5">
                <button
                  v-for="(chunk, ci) in msg.chunks"
                  :key="ci"
                  @click="openSource(chunk, ci)"
                  :class="['group flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-all hover:shadow-md',
                    isCited(msg, ci) ? sourceColor(ci) : 'border-slate-700/50 bg-slate-800/30 hover:border-slate-600']"
                >
                  <span :class="['inline-flex items-center justify-center w-4 h-4 text-[9px] text-white rounded-full font-bold shadow-sm', badgeColor(ci)]">{{ ci + 1 }}</span>
                  <span :class="['truncate max-w-[120px]', isCited(msg, ci) ? 'text-slate-200' : 'text-slate-400 group-hover:text-slate-300']">{{ chunk.metadata.filename ?? '' }}</span>
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- Loading with real-time status -->
        <div v-if="loading" class="flex justify-start">
          <div class="bg-slate-800/60 border border-slate-700/50 rounded-2xl rounded-tl-md px-5 py-3 shadow-lg">
            <div class="flex items-center gap-2">
              <div class="flex gap-1">
                <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 0ms" />
                <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 150ms" />
                <span class="w-1.5 h-1.5 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 300ms" />
              </div>
              <span class="text-slate-400 text-xs">{{ statusText || 'Đang xử lý...' }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Input bar -->
      <div class="flex-shrink-0 px-6 pb-4">
        <div class="flex gap-2 bg-slate-800/50 border border-slate-700/50 rounded-2xl p-1.5 shadow-lg focus-within:border-violet-500/50 transition-all">
          <input v-model="query" @keyup.enter="sendMessage" :disabled="loading"
            placeholder="Nhập câu hỏi về tài liệu..."
            class="flex-1 bg-transparent px-4 py-2 text-slate-100 placeholder-slate-500 focus:outline-none disabled:opacity-50 text-sm" />
          <button @click="sendMessage" :disabled="loading || !query.trim()"
            class="px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-30 rounded-xl text-white transition-all shadow-md shadow-violet-900/30">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Col 3: Source viewer -->
    <div v-if="viewerOpen" class="w-[38%] flex-shrink-0 flex flex-col min-w-0 bg-slate-900/30">
      <!-- Header -->
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
            @click="loadFullDocument(viewerChunks[0] as any)"
            class="px-2 py-1 text-[10px] text-slate-400 hover:text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-600/50 rounded transition-all">
            Xem toàn văn
          </button>
          <button @click="viewerOpen = false; activeSourceIdx = null; highlightChunkId = null"
            class="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-200 hover:bg-slate-700/50 transition-all">&times;</button>
        </div>
      </div>

      <!-- Content -->
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
