<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { listDocuments, deleteDocument, type DocumentInfo } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const collection = ref('documents')
const documents = ref<DocumentInfo[]>([])
const loading = ref(false)
const searchFilter = ref('')
const sortBy = ref<'date' | 'name' | 'chunks'>('date')
const sortAsc = ref(false)
const selectedIds = ref<Set<string>>(new Set())
const deleting = ref(false)

// ── Fetch ───────────────────────────────────────────────────────────────────

async function fetchDocs() {
  loading.value = true
  selectedIds.value.clear()
  try {
    const { data } = await listDocuments(collection.value)
    documents.value = data.documents
  } finally {
    loading.value = false
  }
}

onMounted(fetchDocs)
watch(collection, fetchDocs)

// ── Filtered & sorted ───────────────────────────────────────────────────────

const filtered = computed(() => {
  let docs = documents.value
  const q = searchFilter.value.toLowerCase().trim()
  if (q) {
    docs = docs.filter(d => d.filename.toLowerCase().includes(q))
  }
  docs = [...docs].sort((a, b) => {
    let cmp = 0
    if (sortBy.value === 'name') cmp = a.filename.localeCompare(b.filename)
    else if (sortBy.value === 'chunks') cmp = a.chunkCount - b.chunkCount
    else cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
    return sortAsc.value ? cmp : -cmp
  })
  return docs
})

// ── Stats ───────────────────────────────────────────────────────────────────

const totalChunks = computed(() => documents.value.reduce((sum, d) => sum + d.chunkCount, 0))

const currentCollection = computed(() =>
  store.collections.find(c => c.name === collection.value)
)

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
}

function toggleOne(id: string) {
  if (selectedIds.value.has(id)) {
    selectedIds.value.delete(id)
  } else {
    selectedIds.value.add(id)
  }
  // Trigger reactivity
  selectedIds.value = new Set(selectedIds.value)
}

// ── Delete ──────────────────────────────────────────────────────────────────

async function deleteDoc(doc: DocumentInfo) {
  if (!confirm(`Xoá "${doc.filename}"?`)) return
  await deleteDocument(doc.id, collection.value)
  documents.value = documents.value.filter(d => d.id !== doc.id)
  selectedIds.value.delete(doc.id)
  store.fetch()
}

async function deleteSelected() {
  const ids = [...selectedIds.value]
  const names = documents.value.filter(d => ids.includes(d.id)).map(d => d.filename)
  if (!confirm(`Xoá ${names.length} tài liệu?\n\n${names.slice(0, 5).join('\n')}${names.length > 5 ? `\n...và ${names.length - 5} tài liệu khác` : ''}`)) return

  deleting.value = true
  for (const id of ids) {
    try {
      await deleteDocument(id, collection.value)
      documents.value = documents.value.filter(d => d.id !== id)
    } catch {
      // continue deleting others
    }
  }
  selectedIds.value.clear()
  deleting.value = false
  store.fetch()
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('vi-VN')
}

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
  if (sortBy.value === field) {
    sortAsc.value = !sortAsc.value
  } else {
    sortBy.value = field
    sortAsc.value = field === 'name'
  }
}

function sortIndicator(field: string) {
  if (sortBy.value !== field) return ''
  return sortAsc.value ? ' ↑' : ' ↓'
}
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Filters & stats -->
    <aside class="w-72 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Quản lý tài liệu</h3>

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

      <!-- Search filter -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Lọc theo tên</label>
        <input
          v-model="searchFilter"
          placeholder="Tìm tài liệu..."
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
        />
      </div>

      <!-- Sort -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Sắp xếp</label>
        <div class="flex gap-1.5">
          <button
            @click="setSortBy('date')"
            :class="['px-2.5 py-1.5 rounded-lg text-xs transition-colors', sortBy === 'date' ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700']"
          >Ngày{{ sortIndicator('date') }}</button>
          <button
            @click="setSortBy('name')"
            :class="['px-2.5 py-1.5 rounded-lg text-xs transition-colors', sortBy === 'name' ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700']"
          >Tên{{ sortIndicator('name') }}</button>
          <button
            @click="setSortBy('chunks')"
            :class="['px-2.5 py-1.5 rounded-lg text-xs transition-colors', sortBy === 'chunks' ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-400 hover:bg-slate-700']"
          >Chunks{{ sortIndicator('chunks') }}</button>
        </div>
      </div>

      <!-- Stats -->
      <div class="bg-slate-800 border border-slate-700 rounded-xl p-3 space-y-2">
        <h4 class="text-slate-500 text-xs font-semibold uppercase">Thống kê</h4>
        <div class="grid grid-cols-2 gap-2">
          <div class="text-center">
            <p class="text-xl font-bold text-violet-400">{{ documents.length }}</p>
            <p class="text-slate-500 text-xs">tài liệu</p>
          </div>
          <div class="text-center">
            <p class="text-xl font-bold text-violet-400">{{ totalChunks.toLocaleString() }}</p>
            <p class="text-slate-500 text-xs">chunks</p>
          </div>
        </div>
        <div v-if="currentCollection?.description" class="text-slate-600 text-xs pt-1 border-t border-slate-700">
          {{ currentCollection.description }}
        </div>
      </div>

      <!-- Bulk actions -->
      <div v-if="selectedIds.size > 0" class="space-y-2">
        <p class="text-slate-400 text-xs">Đã chọn {{ selectedIds.size }} tài liệu</p>
        <button
          @click="deleteSelected"
          :disabled="deleting"
          class="w-full py-2 bg-red-600 hover:bg-red-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
        >
          {{ deleting ? 'Đang xoá...' : `Xoá ${selectedIds.size} tài liệu` }}
        </button>
        <button
          @click="selectedIds.clear(); selectedIds = new Set()"
          class="w-full py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300 text-sm transition-colors"
        >Bỏ chọn tất cả</button>
      </div>

      <!-- Refresh -->
      <button
        @click="fetchDocs"
        :disabled="loading"
        class="w-full py-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300 text-sm transition-colors"
      >
        {{ loading ? 'Đang tải...' : 'Làm mới' }}
      </button>
    </aside>

    <!-- Right: Document list -->
    <div class="flex-1 flex flex-col min-w-0">

      <!-- Header row -->
      <div class="flex items-center gap-3 mb-3">
        <label class="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            :checked="allSelected"
            @change="toggleAll"
            class="accent-violet-500"
          />
          <span class="text-slate-500 text-xs">Chọn tất cả</span>
        </label>
        <span class="text-slate-600 text-xs">{{ filtered.length }} tài liệu{{ searchFilter ? ' (đã lọc)' : '' }}</span>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="flex-1 flex items-center justify-center text-slate-500">
        Đang tải...
      </div>

      <!-- Empty -->
      <div v-else-if="filtered.length === 0" class="flex-1 flex items-center justify-center text-slate-600">
        {{ searchFilter ? 'Không tìm thấy tài liệu phù hợp' : 'Chưa có tài liệu nào trong collection này' }}
      </div>

      <!-- Document list -->
      <div v-else class="flex-1 overflow-y-auto space-y-1.5">
        <div
          v-for="doc in filtered"
          :key="doc.id"
          :class="['bg-slate-800 border rounded-xl px-4 py-3 group transition-colors', selectedIds.has(doc.id) ? 'border-violet-500/50 bg-violet-900/10' : 'border-slate-700 hover:border-slate-600']"
        >
          <div class="flex items-center gap-3">
            <!-- Checkbox -->
            <input
              type="checkbox"
              :checked="selectedIds.has(doc.id)"
              @change="toggleOne(doc.id)"
              class="accent-violet-500 flex-shrink-0"
            />

            <!-- Icon -->
            <span class="text-base flex-shrink-0" v-html="fileIcon(doc.filename)" />

            <!-- Info -->
            <div class="flex-1 min-w-0">
              <p class="text-slate-200 text-sm font-medium truncate">{{ doc.filename }}</p>
              <div class="flex items-center gap-3 mt-0.5">
                <span class="text-slate-500 text-xs">{{ doc.chunkCount }} chunks</span>
                <span class="text-slate-600 text-xs">{{ formatRelative(doc.createdAt) }}</span>
                <span class="text-slate-700 text-xs hidden sm:inline" :title="formatDate(doc.createdAt)">{{ formatDate(doc.createdAt) }}</span>
              </div>
            </div>

            <!-- Chunk badge -->
            <span class="text-xs font-mono px-2 py-0.5 rounded-full bg-slate-700 text-slate-400 flex-shrink-0">
              {{ doc.chunkCount }}
            </span>

            <!-- Delete -->
            <button
              @click.stop="deleteDoc(doc)"
              class="text-slate-600 hover:text-red-400 transition-colors text-sm flex-shrink-0 opacity-0 group-hover:opacity-100"
              title="Xoá tài liệu"
            >&times;</button>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>
