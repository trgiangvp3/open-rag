<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { marked } from 'marked'
import { listDocuments, deleteDocument, getDocumentChunks, getDocumentMarkdown, type DocumentInfo, type DocumentChunk } from '../api'
import { useCollectionsStore } from '../stores/collections'

marked.setOptions({ breaks: true, gfm: true })

function renderMd(text: string): string {
  return marked.parse(text, { async: false }) as string
}

const store = useCollectionsStore()
const collection = ref('documents')
const documents = ref<DocumentInfo[]>([])
const loading = ref(false)
const searchFilter = ref('')
const sortBy = ref<'date' | 'name' | 'chunks'>('date')
const sortAsc = ref(false)

// Selected document
const selectedDoc = ref<DocumentInfo | null>(null)
const viewMode = ref<'markdown' | 'chunks'>('markdown')
const markdown = ref('')
const chunks = ref<DocumentChunk[]>([])
const contentLoading = ref(false)

// Bulk selection
const selectedIds = ref<Set<string>>(new Set())
const deleting = ref(false)

// ── Fetch ───────────────────────────────────────────────────────────────────

async function fetchDocs() {
  loading.value = true
  selectedIds.value.clear()
  selectedDoc.value = null
  markdown.value = ''
  chunks.value = []
  try {
    const { data } = await listDocuments(collection.value)
    documents.value = data.documents
  } finally {
    loading.value = false
  }
}

onMounted(fetchDocs)
watch(collection, fetchDocs)

async function selectDocument(doc: DocumentInfo) {
  selectedDoc.value = doc
  markdown.value = ''
  chunks.value = []
  await loadContent()
}

async function loadContent() {
  if (!selectedDoc.value) return
  contentLoading.value = true
  try {
    if (viewMode.value === 'markdown') {
      const { data } = await getDocumentMarkdown(selectedDoc.value.id)
      markdown.value = data.markdown
    } else {
      const { data } = await getDocumentChunks(selectedDoc.value.id, collection.value)
      chunks.value = data.chunks
    }
  } catch {
    markdown.value = ''
    chunks.value = []
  } finally {
    contentLoading.value = false
  }
}

watch(viewMode, () => {
  if (selectedDoc.value) loadContent()
})

// ── Filtered & sorted ───────────────────────────────────────────────────────

const filtered = computed(() => {
  let docs = documents.value
  const q = searchFilter.value.toLowerCase().trim()
  if (q) docs = docs.filter(d => d.filename.toLowerCase().includes(q))
  docs = [...docs].sort((a, b) => {
    let cmp = 0
    if (sortBy.value === 'name') cmp = a.filename.localeCompare(b.filename)
    else if (sortBy.value === 'chunks') cmp = a.chunkCount - b.chunkCount
    else cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
    return sortAsc.value ? cmp : -cmp
  })
  return docs
})

const totalChunks = computed(() => documents.value.reduce((sum, d) => sum + d.chunkCount, 0))

// ── Selection ───────────────────────────────────────────────────────────────

const allSelected = computed(() =>
  filtered.value.length > 0 && filtered.value.every(d => selectedIds.value.has(d.id))
)

function toggleAll() {
  if (allSelected.value) {
    selectedIds.value.clear()
  } else {
    selectedIds.value = new Set(filtered.value.map(d => d.id))
  }
  selectedIds.value = new Set(selectedIds.value)
}

function toggleOne(id: string) {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
  selectedIds.value = new Set(selectedIds.value)
}

// ── Delete ──────────────────────────────────────────────────────────────────

async function deleteDoc(doc: DocumentInfo) {
  if (!confirm(`Xoá "${doc.filename}"?`)) return
  await deleteDocument(doc.id, collection.value)
  documents.value = documents.value.filter(d => d.id !== doc.id)
  if (selectedDoc.value?.id === doc.id) {
    selectedDoc.value = null; markdown.value = ''; chunks.value = []
  }
  store.fetch()
}

async function deleteSelected() {
  const ids = [...selectedIds.value]
  const names = documents.value.filter(d => ids.includes(d.id)).map(d => d.filename)
  if (!confirm(`Xoá ${names.length} tài liệu?\n\n${names.slice(0, 5).join('\n')}${names.length > 5 ? `\n...và ${names.length - 5} tài liệu khác` : ''}`)) return
  deleting.value = true
  for (const id of ids) {
    try { await deleteDocument(id, collection.value); documents.value = documents.value.filter(d => d.id !== id) } catch { /* continue */ }
  }
  selectedIds.value.clear()
  if (selectedDoc.value && ids.includes(selectedDoc.value.id)) {
    selectedDoc.value = null; markdown.value = ''; chunks.value = []
  }
  deleting.value = false
  store.fetch()
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string) { return new Date(iso).toLocaleString('vi-VN') }

function formatRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'vừa xong'
  if (mins < 60) return `${mins} phút trước`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} giờ trước`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} ngày trước`
  return formatDate(iso)
}

function fileIcon(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase()
  const icons: Record<string, string> = {
    pdf: '&#128211;', docx: '&#128196;', doc: '&#128196;',
    xlsx: '&#128202;', xls: '&#128202;', csv: '&#128202;',
    pptx: '&#128218;', ppt: '&#128218;',
    txt: '&#128209;', md: '&#128209;',
    html: '&#127760;', htm: '&#127760;',
  }
  return icons[ext ?? ''] ?? '&#128196;'
}

function setSortBy(field: 'date' | 'name' | 'chunks') {
  if (sortBy.value === field) sortAsc.value = !sortAsc.value
  else { sortBy.value = field; sortAsc.value = field === 'name' }
}

function sortIndicator(field: string) {
  return sortBy.value !== field ? '' : sortAsc.value ? ' ↑' : ' ↓'
}
</script>

<template>
  <div class="flex gap-0 h-[calc(100vh-8rem)]">

    <!-- Col 1: Filters -->
    <div class="w-56 flex-shrink-0 border-r border-slate-700 flex flex-col">
      <div class="p-3 space-y-2">
        <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Tài liệu</h3>

        <select v-model="collection"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-2 py-1.5 text-slate-300 text-xs focus:outline-none focus:border-violet-500">
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>

        <input v-model="searchFilter" placeholder="Lọc tên file..."
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-2 py-1.5 text-slate-100 placeholder-slate-500 text-xs focus:outline-none focus:border-violet-500" />

        <div class="flex gap-1">
          <button v-for="f in (['date', 'name', 'chunks'] as const)" :key="f" @click="setSortBy(f)"
            :class="['px-2 py-1 rounded text-xs transition-colors', sortBy === f ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-500 hover:bg-slate-700']"
          >{{ { date: 'Ngày', name: 'Tên', chunks: 'Chunks' }[f] }}{{ sortIndicator(f) }}</button>
        </div>

        <div class="bg-slate-800 border border-slate-700 rounded-lg p-2 space-y-1">
          <div class="flex justify-between text-xs">
            <span class="text-slate-500">Tài liệu</span>
            <span class="text-violet-400 font-mono">{{ documents.length }}</span>
          </div>
          <div class="flex justify-between text-xs">
            <span class="text-slate-500">Tổng chunks</span>
            <span class="text-violet-400 font-mono">{{ totalChunks.toLocaleString() }}</span>
          </div>
        </div>

        <div v-if="selectedIds.size > 0" class="space-y-1.5">
          <p class="text-slate-400 text-xs">Đã chọn {{ selectedIds.size }}</p>
          <button @click="deleteSelected" :disabled="deleting"
            class="w-full py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded-lg text-white text-xs transition-colors">
            {{ deleting ? 'Đang xoá...' : 'Xoá đã chọn' }}
          </button>
          <button @click="selectedIds.clear(); selectedIds = new Set()"
            class="w-full py-1.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300 text-xs transition-colors">Bỏ chọn</button>
        </div>

        <button @click="fetchDocs" :disabled="loading"
          class="w-full py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300 text-xs transition-colors">
          {{ loading ? 'Đang tải...' : 'Làm mới' }}
        </button>
      </div>
    </div>

    <!-- Col 2: Document list -->
    <div class="w-72 flex-shrink-0 border-r border-slate-700 flex flex-col">
      <!-- Select all header -->
      <div v-if="filtered.length > 0" class="px-3 py-2 border-b border-slate-800 flex items-center gap-2">
        <input type="checkbox" :checked="allSelected" @change="toggleAll" class="accent-violet-500" />
        <span class="text-slate-500 text-xs">Chọn tất cả ({{ filtered.length }})</span>
      </div>
      <div v-if="loading" class="flex-1 flex items-center justify-center text-slate-500 text-sm">Đang tải...</div>
      <div v-else-if="filtered.length === 0" class="flex-1 flex items-center justify-center text-slate-600 text-xs px-3 text-center">
        {{ searchFilter ? 'Không tìm thấy' : 'Chưa có tài liệu' }}
      </div>
      <div v-else class="flex-1 overflow-y-auto">
        <div v-for="doc in filtered" :key="doc.id" @click="selectDocument(doc)"
          :class="['px-3 py-2.5 cursor-pointer border-b border-slate-800 group transition-colors',
            selectedDoc?.id === doc.id ? 'bg-violet-900/20 border-l-2 border-l-violet-500' : 'hover:bg-slate-800/50']">
          <div class="flex items-center gap-2">
            <input type="checkbox" :checked="selectedIds.has(doc.id)" @click.stop="toggleOne(doc.id)" class="accent-violet-500 flex-shrink-0" />
            <span class="text-sm flex-shrink-0" v-html="fileIcon(doc.filename)" />
            <div class="flex-1 min-w-0">
              <p class="text-slate-300 text-xs font-medium truncate">{{ doc.filename }}</p>
              <p class="text-slate-600 text-xs">{{ doc.chunkCount }} chunks · {{ formatRelative(doc.createdAt) }}</p>
            </div>
            <button @click.stop="deleteDoc(doc)"
              class="text-slate-700 hover:text-red-400 text-sm opacity-0 group-hover:opacity-100 flex-shrink-0">&times;</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Col 3: Viewer -->
    <div class="flex-1 flex flex-col min-w-0">
      <div v-if="!selectedDoc" class="flex-1 flex items-center justify-center text-slate-600 text-sm">
        Chọn tài liệu để xem nội dung
      </div>
      <template v-else>
        <!-- Header with view mode toggle -->
        <div class="p-3 border-b border-slate-700 flex items-center justify-between">
          <div class="min-w-0">
            <p class="text-slate-200 text-sm font-medium truncate">{{ selectedDoc.filename }}</p>
            <p class="text-slate-500 text-xs">{{ selectedDoc.chunkCount }} chunks · {{ formatDate(selectedDoc.createdAt) }}</p>
          </div>
          <div class="flex bg-slate-800 rounded-lg p-0.5">
            <button @click="viewMode = 'markdown'"
              :class="['px-3 py-1 rounded text-xs transition-colors', viewMode === 'markdown' ? 'bg-violet-600 text-white' : 'text-slate-400 hover:text-slate-200']"
            >Markdown</button>
            <button @click="viewMode = 'chunks'"
              :class="['px-3 py-1 rounded text-xs transition-colors', viewMode === 'chunks' ? 'bg-violet-600 text-white' : 'text-slate-400 hover:text-slate-200']"
            >Chunks</button>
          </div>
        </div>

        <!-- Content -->
        <div v-if="contentLoading" class="flex-1 flex items-center justify-center text-slate-500 text-sm">Đang tải...</div>

        <!-- Markdown view -->
        <div v-else-if="viewMode === 'markdown'" class="flex-1 overflow-y-auto p-6">
          <div v-if="markdown" class="prose prose-invert prose-sm max-w-none" v-html="renderMd(markdown)" />
          <div v-else class="text-slate-600 text-sm">Không có nội dung markdown (tài liệu cũ chưa lưu markdown)</div>
        </div>

        <!-- Chunks view -->
        <div v-else class="flex-1 overflow-y-auto">
          <div v-if="chunks.length === 0" class="flex-1 flex items-center justify-center text-slate-600 text-sm p-6">Không có chunks</div>
          <div v-for="(chunk, i) in chunks" :key="chunk.id" class="border-b border-slate-800">
            <div class="px-4 py-2 bg-slate-800/50 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-violet-400 text-xs font-mono">#{{ i }}</span>
                <span v-if="chunk.metadata?.section" class="text-slate-400 text-xs">{{ chunk.metadata.section }}</span>
              </div>
              <span class="text-slate-600 text-xs">{{ chunk.text.length }} ký tự</span>
            </div>
            <div class="px-4 py-3">
              <div class="prose prose-invert prose-sm max-w-none" v-html="renderMd(chunk.text)" />
            </div>
          </div>
        </div>
      </template>
    </div>

  </div>
</template>
