<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { chat, getChatHistory, deleteChatSession, type ChunkResult, type ChatHistoryResponse } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const collection = ref('documents')
const useReranker = ref(false)
const searchMode = ref('semantic')

const SESSION_KEY = 'openrag_chat_session_id'

interface Message {
  role: 'user' | 'assistant'
  content: string
  chunks?: ChunkResult[]
  citations?: number[]
  showSources?: boolean
}

const sessionId = ref<string | null>(localStorage.getItem(SESSION_KEY))
const messages = ref<Message[]>([])
const query = ref('')
const loading = ref(false)
const error = ref('')
const messagesEl = ref<HTMLElement | null>(null)

// Load existing session history on mount
onMounted(async () => {
  if (sessionId.value) {
    try {
      const { data } = await getChatHistory(sessionId.value)
      messages.value = data.messages.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))
    } catch {
      // Session may have been deleted — start fresh
      sessionId.value = null
      localStorage.removeItem(SESSION_KEY)
    }
  }
})

async function sendMessage() {
  if (!query.value.trim() || loading.value) return
  const userQuery = query.value
  query.value = ''

  messages.value.push({ role: 'user', content: userQuery })
  loading.value = true
  error.value = ''
  await scrollToBottom()

  try {
    const { data } = await chat({
      query: userQuery,
      collection: collection.value,
      sessionId: sessionId.value ?? undefined,
      topK: 5,
      useReranker: useReranker.value,
      searchMode: searchMode.value,
    })

    sessionId.value = data.sessionId
    localStorage.setItem(SESSION_KEY, data.sessionId)

    messages.value.push({
      role: 'assistant',
      content: data.answer ?? '*(Không có câu trả lời — LLM chưa được cấu hình)*',
      chunks: data.chunks,
      citations: data.citations ?? [],
      showSources: false,
    })
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? e.message
    messages.value.push({ role: 'assistant', content: `*Lỗi: ${error.value}*` })
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

function newConversation() {
  if (sessionId.value) {
    deleteChatSession(sessionId.value).catch(() => {})
  }
  sessionId.value = null
  localStorage.removeItem(SESSION_KEY)
  messages.value = []
  error.value = ''
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value)
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

function renderAnswer(text: string) {
  return text.replace(/\[(\d+)\]/g,
    '<span class="inline-flex items-center justify-center w-5 h-5 text-xs bg-violet-600 text-white rounded-full font-bold mx-0.5">$1</span>')
}

function scoreColor(score: number) {
  if (score >= 0.5) return 'text-green-400'
  if (score >= 0.3) return 'text-yellow-400'
  return 'text-red-400'
}
</script>

<template>
  <div class="flex flex-col h-[calc(100vh-10rem)]">
    <!-- Header bar -->
    <div class="flex items-center justify-between gap-4 mb-3 flex-shrink-0">
      <div class="flex flex-wrap gap-3 text-sm text-slate-400">
        <select v-model="collection" class="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-300 focus:outline-none focus:border-violet-500">
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
        <select v-model="searchMode" class="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-slate-300 focus:outline-none focus:border-violet-500">
          <option value="semantic">Semantic</option>
          <option value="hybrid">Hybrid</option>
        </select>
        <label class="flex items-center gap-1.5 cursor-pointer select-none">
          <input type="checkbox" v-model="useReranker" class="accent-violet-500" />
          Reranker
        </label>
      </div>
      <button
        @click="newConversation"
        class="text-xs text-slate-400 hover:text-slate-200 border border-slate-600 rounded px-3 py-1 transition-colors"
      >
        Cuộc hội thoại mới
      </button>
    </div>

    <!-- Message list -->
    <div ref="messagesEl" class="flex-1 overflow-y-auto space-y-4 pr-1 mb-3">
      <!-- Empty state -->
      <div v-if="!messages.length" class="flex flex-col items-center justify-center h-full text-slate-600">
        <svg class="w-12 h-12 mb-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
        </svg>
        <p>Bắt đầu hội thoại với tài liệu của bạn</p>
      </div>

      <template v-for="(msg, i) in messages" :key="i">
        <!-- User message -->
        <div v-if="msg.role === 'user'" class="flex justify-end">
          <div class="max-w-[75%] bg-violet-700/50 border border-violet-600/50 rounded-2xl rounded-tr-sm px-4 py-2.5 text-slate-100 text-sm leading-relaxed">
            {{ msg.content }}
          </div>
        </div>

        <!-- Assistant message -->
        <div v-else class="flex justify-start">
          <div class="max-w-[85%] space-y-2">
            <div class="bg-slate-800 border border-slate-700 rounded-2xl rounded-tl-sm px-4 py-2.5">
              <div class="text-slate-200 text-sm leading-relaxed" v-html="renderAnswer(msg.content)" />
            </div>

            <!-- Sources toggle -->
            <div v-if="msg.chunks?.length" class="px-1">
              <button
                @click="msg.showSources = !msg.showSources"
                class="text-xs text-slate-500 hover:text-violet-400 flex items-center gap-1 transition-colors"
              >
                <svg :class="['w-3 h-3 transition-transform', msg.showSources ? 'rotate-90' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                {{ msg.chunks.length }} nguồn tham khảo
                <span v-if="msg.citations?.length" class="text-violet-400">({{ msg.citations.length }} được trích dẫn)</span>
              </button>

              <div v-if="msg.showSources" class="mt-2 space-y-2">
                <div
                  v-for="(chunk, ci) in msg.chunks"
                  :key="ci"
                  :class="['rounded-lg p-3 border text-xs', msg.citations?.includes(ci) ? 'bg-violet-900/20 border-violet-700/50' : 'bg-slate-800/50 border-slate-700']"
                >
                  <div class="flex items-center justify-between mb-1">
                    <span class="text-violet-400 font-medium truncate">{{ chunk.metadata.filename ?? 'Unknown' }}</span>
                    <span :class="['font-mono', scoreColor(chunk.score)]">{{ (chunk.score * 100).toFixed(0) }}%</span>
                  </div>
                  <p class="text-slate-400 line-clamp-3 leading-relaxed">{{ chunk.text }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- Loading indicator -->
      <div v-if="loading" class="flex justify-start">
        <div class="bg-slate-800 border border-slate-700 rounded-2xl rounded-tl-sm px-4 py-3">
          <div class="flex gap-1">
            <span class="w-2 h-2 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 0ms" />
            <span class="w-2 h-2 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 150ms" />
            <span class="w-2 h-2 bg-violet-400 rounded-full animate-bounce" style="animation-delay: 300ms" />
          </div>
        </div>
      </div>
    </div>

    <!-- Input bar -->
    <div class="flex-shrink-0 flex gap-2">
      <input
        v-model="query"
        @keyup.enter="sendMessage"
        :disabled="loading"
        placeholder="Nhập câu hỏi..."
        class="flex-1 bg-slate-800 border border-slate-600 rounded-xl px-4 py-2.5 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500 disabled:opacity-50"
      />
      <button
        @click="sendMessage"
        :disabled="loading || !query.trim()"
        class="px-4 py-2.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-xl text-white font-medium transition-colors"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
        </svg>
      </button>
    </div>
  </div>
</template>
