<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick, onBeforeUnmount, shallowRef } from 'vue'
import { listDocuments, deleteDocument, getDocumentChunks, getDocumentMarkdown, getDocumentMetadata, updateDocumentMetadata, listDomains, uploadFile, ingestText, createCollection, deleteCollection, updateCollectionSettings, testHeadingScript, type DocumentInfo, type DocumentChunk, type DomainInfo, type TestScriptResponse } from '../api'
import { useCollectionsStore } from '../stores/collections'
import { useProgressHub } from '../composables/useProgressHub'
import { renderMdPlain as renderMd } from '../utils/markdown'
import * as monaco from 'monaco-editor'

const store = useCollectionsStore()
const collection = ref('documents')
const documents = ref<DocumentInfo[]>([])
const loading = ref(false)
const searchFilter = ref('')
const sortBy = ref<'date' | 'name' | 'chunks'>('date')
const sortAsc = ref(false)

// Document viewer (slide-over)
const selectedDoc = ref<DocumentInfo | null>(null)
const viewMode = ref<'markdown' | 'chunks' | 'metadata'>('markdown')
const markdown = ref('')
const chunks = ref<DocumentChunk[]>([])
const contentLoading = ref(false)

// Metadata editing
const metaForm = ref<Record<string, unknown>>({})
const metaSaving = ref(false)
const metaMsg = ref('')
const allDomains = ref<DomainInfo[]>([])

async function loadMetadata() {
  if (!selectedDoc.value) return
  try {
    const { data } = await getDocumentMetadata(selectedDoc.value.id)
    metaForm.value = { ...data }
    metaMsg.value = ''
  } catch { metaMsg.value = 'Lỗi tải metadata' }
}

async function saveMetadata() {
  if (!selectedDoc.value) return
  metaSaving.value = true; metaMsg.value = ''
  try {
    const { data } = await updateDocumentMetadata(selectedDoc.value.id, metaForm.value)
    metaMsg.value = `Đã lưu (${data.chunksUpdated} chunks cập nhật)`
    fetchDocs()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string }
    metaMsg.value = 'Lỗi: ' + (err.response?.data?.detail ?? err.message)
  } finally { metaSaving.value = false }
}

onMounted(async () => {
  try { allDomains.value = (await listDomains()).data.domains } catch {}
})
const flatDomains = computed(() => {
  const result: { id: number; name: string }[] = []
  for (const d of allDomains.value) {
    result.push({ id: d.id, name: d.name })
    for (const c of d.children ?? []) result.push({ id: c.id, name: `  └ ${c.name}` })
  }
  return result
})

// Bulk selection
const selectedIds = ref<Set<string>>(new Set())
const deleting = ref(false)

// Modal state
const modal = ref<'upload' | 'settings' | null>(null)

// ── Upload ─────────────────────────────────────────────────────────────────
type ItemStatus = 'pending' | 'uploading' | 'done' | 'error'
interface QueueItem {
  id: number; type: 'file' | 'text'; label: string
  file?: File; textTitle?: string; textContent?: string
  status: ItemStatus; message: string; documentId?: string; retries: number
}
const MAX_RETRIES = 3
let nextQueueId = 0
const queue = ref<QueueItem[]>([])
const dragging = ref(false)
const processing = ref(false)
const retryRound = ref(0)
const textTitle = ref('')
const textContent = ref('')
const hasPending = computed(() => queue.value.some(q => q.status === 'pending'))

interface ProgressInfo { stage: string; progress: number }
const progressMap = ref<Map<string, ProgressInfo>>(new Map())
const { connect: ensureHub } = useProgressHub('progress', (event: { documentId: string; stage: string; progress: number; filename?: string }) => {
  // Index by both documentId and filename so we can match before API response
  progressMap.value = new Map(progressMap.value)
  progressMap.value.set(event.documentId, { stage: event.stage, progress: event.progress })
  if (event.filename) progressMap.value.set(event.filename, { stage: event.stage, progress: event.progress })
})

function onDrop(e: DragEvent) { dragging.value = false; if (e.dataTransfer?.files) addFiles(Array.from(e.dataTransfer.files)) }
function onFileChange(e: Event) { const input = e.target as HTMLInputElement; if (input.files) addFiles(Array.from(input.files)); input.value = '' }
function addFiles(files: File[]) { for (const f of files) queue.value.push({ id: nextQueueId++, type: 'file', label: f.name, file: f, status: 'pending', message: '', retries: 0 }) }
function addText() {
  const content = textContent.value.trim(); if (!content) return
  const title = textTitle.value.trim() || 'untitled'
  queue.value.push({ id: nextQueueId++, type: 'text', label: title, textTitle: title, textContent: content, status: 'pending', message: '', retries: 0 })
  textTitle.value = ''; textContent.value = ''
}
function removeQueueItem(id: number) { queue.value = queue.value.filter(q => q.id !== id) }
function clearDone() { queue.value = queue.value.filter(q => q.status !== 'done' && q.status !== 'error') }

async function processItem(item: QueueItem) {
  item.status = 'uploading'; item.message = ''
  try {
    if (item.type === 'file' && item.file) { const { data } = await uploadFile(item.file, collection.value); item.documentId = data.documentId; item.message = data.message }
    else if (item.type === 'text' && item.textContent) { const { data } = await ingestText(item.textContent, item.textTitle || 'untitled', collection.value); item.documentId = data.documentId; item.message = data.message }
    item.status = 'done'
  } catch (e: unknown) { const err = e as { response?: { data?: { detail?: string } }; message?: string }; item.status = 'error'; item.retries++; item.message = err.response?.data?.detail ?? err.message ?? 'Thất bại' }
}

async function startUpload() {
  processing.value = true; retryRound.value = 0; await ensureHub()
  for (const item of queue.value.filter(q => q.status === 'pending')) await processItem(item)
  while (true) { const errors = queue.value.filter(q => q.status === 'error' && q.retries < MAX_RETRIES); if (!errors.length) break; retryRound.value++; for (const item of errors) { item.message = `Thử lại ${item.retries + 1}/${MAX_RETRIES}...`; await processItem(item) } }
  processing.value = false; retryRound.value = 0; store.fetch(); fetchDocs()
}
function getProgress(item: QueueItem): ProgressInfo | null {
  return progressMap.value.get(item.label) ?? (item.documentId ? progressMap.value.get(item.documentId) ?? null : null)
}
function stageLabel(stage: string) { return { converting: 'Chuyển đổi...', chunking: 'Phân đoạn...', embedding: 'Nhúng...', done: 'Hoàn thành' }[stage] ?? stage }
const statusClass = (s: ItemStatus) => ({ pending: 'th-text2', uploading: 'text-yellow-400 animate-pulse', done: 'text-green-400', error: 'text-red-400' }[s])

// ── Collection settings ────────────────────────────────────────────────────
const newColName = ref(''); const newColDesc = ref(''); const creatingCol = ref(false); const colMessage = ref('')
const chunkSize = ref(400); const chunkOverlap = ref(50); const sectionTokenThreshold = ref(800)
const autoDetectHeadings = ref(true); const headingScript = ref<string | null>(null)
const savingCol = ref(false); const saveColMsg = ref('')
const testSampleText = ref(''); const testResult = ref<TestScriptResponse | null>(null); const testingChunk = ref(false)
const editorContainer = ref<HTMLElement | null>(null); const editorInstance = shallowRef<monaco.editor.IStandaloneCodeEditor | null>(null)

const defaultScript = `function detectHeading(line, index, allLines) {
  var clean = line.replace(/\\*\\*/g, '').trim();
  if (!clean) return null;
  if (/^Ch[uư][oơ]ng\\s+[IVXLCDM\\d]+/i.test(clean)) {
    var title = clean;
    if (index + 1 < allLines.length) {
      var next = allLines[index + 1].replace(/\\*\\*/g, '').trim();
      if (next && next.length < 100 && !/^(Ch[uư]|M[uụ]c|[ĐD]i[eề]u|\\d+[.)]|[a-zđ][.)])/i.test(next))
        title = clean + ' — ' + next;
    }
    return { level: 1, text: title };
  }
  if (/^M[uụ]c\\s+\\d+/i.test(clean)) return { level: 2, text: clean };
  if (/^[ĐD]i[eề]u\\s+\\d+/i.test(clean)) return { level: 3, text: clean };
  var k = clean.match(/^(\\d+)[.)]/); if (k) return { level: 4, text: 'Khoản ' + k[1] };
  var d = clean.match(/^([a-zđ])[.)]/); if (d) return { level: 5, text: 'Điểm ' + d[1] };
  return null;
}`

function openSettings() {
  modal.value = 'settings'
  const col = store.collections.find(c => c.name === collection.value)
  if (col) { chunkSize.value = col.chunkSize; chunkOverlap.value = col.chunkOverlap; sectionTokenThreshold.value = col.sectionTokenThreshold; autoDetectHeadings.value = col.autoDetectHeadings; headingScript.value = col.headingScript }
  saveColMsg.value = ''; testResult.value = null
  nextTick(initEditor)
}
function initEditor() {
  editorInstance.value?.dispose(); editorInstance.value = null; if (!editorContainer.value) return
  const editor = monaco.editor.create(editorContainer.value, { value: headingScript.value ?? defaultScript, language: 'javascript', theme: 'vs-dark', minimap: { enabled: false }, lineNumbers: 'on', fontSize: 12, tabSize: 2, scrollBeyondLastLine: false, wordWrap: 'on', automaticLayout: true, padding: { top: 8, bottom: 8 } })
  editor.onDidChangeModelContent(() => { const val = editor.getValue(); headingScript.value = val === defaultScript ? null : val })
  editorInstance.value = editor
}
onBeforeUnmount(() => { editorInstance.value?.dispose() })

async function createCol() {
  if (!newColName.value.trim()) return; creatingCol.value = true; colMessage.value = ''
  try { await createCollection(newColName.value.trim(), newColDesc.value.trim()); colMessage.value = `Đã tạo "${newColName.value}"`; collection.value = newColName.value.trim(); newColName.value = ''; newColDesc.value = ''; store.fetch() }
  catch (e: unknown) { const err = e as { response?: { data?: { message?: string } }; message?: string }; colMessage.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message) }
  finally { creatingCol.value = false }
}
async function removeCol(name: string) {
  if (!confirm(`Xoá collection "${name}" và toàn bộ tài liệu?`)) return
  try { await deleteCollection(name); if (collection.value === name) collection.value = 'documents'; store.fetch() }
  catch (e: unknown) { const err = e as { response?: { data?: { message?: string } }; message?: string }; colMessage.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message) }
}
async function saveColSettings() {
  savingCol.value = true; saveColMsg.value = ''
  try { await updateCollectionSettings(collection.value, { chunkSize: chunkSize.value, chunkOverlap: chunkOverlap.value, sectionTokenThreshold: sectionTokenThreshold.value, autoDetectHeadings: autoDetectHeadings.value, headingScript: headingScript.value }); saveColMsg.value = 'Đã lưu!'; store.fetch() }
  catch (e: unknown) { const err = e as { response?: { data?: { message?: string } }; message?: string }; saveColMsg.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message) }
  finally { savingCol.value = false }
}
async function runTest() {
  if (!testSampleText.value.trim()) return; testingChunk.value = true; testResult.value = null
  try { const { data } = await testHeadingScript(collection.value, headingScript.value ?? '', testSampleText.value, { chunkSize: chunkSize.value, chunkOverlap: chunkOverlap.value, sectionTokenThreshold: sectionTokenThreshold.value, autoDetectHeadings: autoDetectHeadings.value }); testResult.value = data }
  catch (e: unknown) { const err = e as { response?: { data?: { message?: string } }; message?: string }; saveColMsg.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message) }
  finally { testingChunk.value = false }
}

// ── Document management ────────────────────────────────────────────────────
let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchDocs() {
  loading.value = true; selectedIds.value.clear(); selectedDoc.value = null
  try { documents.value = (await listDocuments(collection.value)).data.documents }
  finally { loading.value = false }
  startPollIfNeeded()
}

function startPollIfNeeded() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (documents.value.some(d => d.status === 'indexing')) {
    pollTimer = setInterval(async () => {
      try {
        const { data } = await listDocuments(collection.value)
        documents.value = data.documents
        if (!data.documents.some((d: DocumentInfo) => d.status === 'indexing')) {
          clearInterval(pollTimer!); pollTimer = null
        }
      } catch { /* ignore */ }
    }, 3000)
  }
}

onMounted(() => { ensureHub(); fetchDocs() })
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
watch(collection, fetchDocs)

function selectDocument(doc: DocumentInfo) { selectedDoc.value = doc; markdown.value = ''; chunks.value = []; loadContent() }
function closeViewer() { selectedDoc.value = null }

async function loadContent() {
  if (!selectedDoc.value) return; contentLoading.value = true
  try {
    if (viewMode.value === 'markdown') markdown.value = (await getDocumentMarkdown(selectedDoc.value.id)).data.markdown
    else if (viewMode.value === 'chunks') chunks.value = (await getDocumentChunks(selectedDoc.value.id, collection.value)).data.chunks
    else if (viewMode.value === 'metadata') await loadMetadata()
  } catch (e) { console.warn('Failed to load content:', e); markdown.value = ''; chunks.value = [] }
  finally { contentLoading.value = false }
}
watch(viewMode, () => { if (selectedDoc.value) loadContent() })

const filtered = computed(() => {
  let docs = documents.value; const q = searchFilter.value.toLowerCase().trim()
  if (q) docs = docs.filter(d => d.filename.toLowerCase().includes(q))
  return [...docs].sort((a, b) => {
    let cmp = sortBy.value === 'name' ? a.filename.localeCompare(b.filename) : sortBy.value === 'chunks' ? a.chunkCount - b.chunkCount : new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime()
    return sortAsc.value ? cmp : -cmp
  })
})
const totalChunks = computed(() => documents.value.reduce((sum, d) => sum + d.chunkCount, 0))
const allSelected = computed(() => filtered.value.length > 0 && filtered.value.every(d => selectedIds.value.has(d.id)))
function toggleAll() { selectedIds.value = allSelected.value ? new Set() : new Set(filtered.value.map(d => d.id)) }
function toggleOne(id: string) { const s = new Set(selectedIds.value); s.has(id) ? s.delete(id) : s.add(id); selectedIds.value = s }

async function deleteDoc(doc: DocumentInfo) {
  if (!confirm(`Xoá "${doc.filename}"?`)) return
  await deleteDocument(doc.id, collection.value); documents.value = documents.value.filter(d => d.id !== doc.id)
  if (selectedDoc.value?.id === doc.id) selectedDoc.value = null; store.fetch()
}
async function deleteSelected() {
  const ids = [...selectedIds.value]; const names = documents.value.filter(d => ids.includes(d.id)).map(d => d.filename)
  if (!confirm(`Xoá ${names.length} tài liệu?\n\n${names.slice(0, 5).join('\n')}${names.length > 5 ? `\n...+${names.length - 5}` : ''}`)) return
  deleting.value = true
  for (const id of ids) { try { await deleteDocument(id, collection.value); documents.value = documents.value.filter(d => d.id !== id) } catch {} }
  selectedIds.value.clear(); if (selectedDoc.value && ids.includes(selectedDoc.value.id)) selectedDoc.value = null
  deleting.value = false; store.fetch()
}

function formatRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime(); const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'vừa xong'; if (mins < 60) return `${mins}p trước`
  const hours = Math.floor(mins / 60); if (hours < 24) return `${hours}h trước`
  return `${Math.floor(hours / 24)}d trước`
}
function setSortBy(field: 'date' | 'name' | 'chunks') { if (sortBy.value === field) sortAsc.value = !sortAsc.value; else { sortBy.value = field; sortAsc.value = field === 'name' } }
function sortIndicator(field: string) { return sortBy.value !== field ? '' : sortAsc.value ? ' ↑' : ' ↓' }
</script>

<template>
  <div class="h-[calc(100vh-4rem)] flex flex-col">

    <!-- Toolbar -->
    <div class="flex items-center justify-between pb-4 flex-shrink-0">
      <div class="flex items-center gap-3">
        <select v-model="collection"
          class="th-elevated border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none focus:th-border">
          <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }} ({{ c.documentCount }})</option>
        </select>
        <div class="relative">
          <svg class="w-4 h-4 th-text3 absolute left-3 top-1/2 -translate-y-1/2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
          <input v-model="searchFilter" placeholder="Tìm tài liệu..."
            class="th-elevated border th-border rounded-lg pl-9 pr-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border w-64" />
        </div>
        <span class="th-text3 text-xs">{{ documents.length }} tài liệu · {{ totalChunks.toLocaleString() }} chunks</span>
      </div>
      <div class="flex items-center gap-2">
        <div v-if="selectedIds.size > 0" class="flex items-center gap-2 mr-2">
          <span class="th-text2 text-xs">{{ selectedIds.size }} đã chọn</span>
          <button @click="deleteSelected" :disabled="deleting" class="px-3 py-1.5 bg-red-600/20 text-red-400 hover:bg-red-600/30 rounded-lg text-xs transition-colors">Xoá</button>
          <button @click="selectedIds.clear(); selectedIds = new Set()" class="th-text3 hover:th-text text-xs">Bỏ chọn</button>
        </div>
        <button @click="modal = 'upload'" class="px-4 py-2 th-btn hover:th-btn rounded-lg text-white text-sm font-medium transition-colors">+ Thêm tài liệu</button>
        <button @click="openSettings" class="p-2 th-elevated hover:th-bg3 border th-border rounded-lg th-text2 hover:th-text transition-colors" title="Cấu hình collection">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
        </button>
        <button @click="fetchDocs" :disabled="loading" class="p-2 th-elevated hover:th-bg3 border th-border rounded-lg th-text2 hover:th-text transition-colors" title="Làm mới">
          <svg :class="['w-4 h-4', loading && 'animate-spin']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
        </button>
      </div>
    </div>

    <!-- Main content: table + slide-over viewer -->
    <div class="flex-1 flex gap-0 min-h-0">

      <!-- Document table -->
      <div :class="['flex-1 flex flex-col min-w-0 th-elevated border th-border rounded-xl overflow-hidden', selectedDoc && 'rounded-r-none border-r-0']">
        <!-- Table header -->
        <div class="flex items-center gap-3 px-4 py-2.5 border-b th-border th-elevated text-xs th-text3 flex-shrink-0">
          <input type="checkbox" :checked="allSelected" @change="toggleAll" class="accent-[var(--accent)]" />
          <button @click="setSortBy('name')" class="flex-1 text-left hover:th-text transition-colors">Tên file{{ sortIndicator('name') }}</button>
          <button @click="setSortBy('chunks')" class="w-20 text-right hover:th-text transition-colors">Chunks{{ sortIndicator('chunks') }}</button>
          <button @click="setSortBy('date')" class="w-24 text-right hover:th-text transition-colors">Ngày tạo{{ sortIndicator('date') }}</button>
          <span class="w-8"></span>
        </div>

        <!-- Table body -->
        <div v-if="loading" class="flex-1 flex items-center justify-center th-text3 text-sm">Đang tải...</div>
        <div v-else-if="filtered.length === 0" class="flex-1 flex items-center justify-center th-text3 text-sm">
          {{ searchFilter ? 'Không tìm thấy' : 'Chưa có tài liệu. Bấm "+ Thêm tài liệu" để bắt đầu.' }}
        </div>
        <div v-else class="flex-1 overflow-y-auto">
          <div v-for="doc in filtered" :key="doc.id" @click="selectDocument(doc)"
            :class="['flex items-center gap-3 px-4 py-3 cursor-pointer border-b th-border2 group transition-colors',
              selectedDoc?.id === doc.id ? 'th-active' : 'hover:th-elevated']">
            <input type="checkbox" :checked="selectedIds.has(doc.id)" @click.stop="toggleOne(doc.id)" class="accent-[var(--accent)] flex-shrink-0" />
            <div class="flex-1 min-w-0">
              <p class="th-text text-sm truncate">
                {{ doc.filename }}
                <span v-if="doc.status === 'indexing'" class="inline-flex items-center gap-1 ml-1 text-[10px] px-1.5 py-0.5 rounded-full font-medium" style="background: var(--score-mid-bg); color: var(--score-mid-text)">
                  <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" /><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  đang xử lý
                </span>
                <span v-else-if="doc.status === 'failed'" class="text-[10px] px-1.5 py-0.5 rounded-full font-medium" style="background: var(--score-low-bg); color: var(--score-low-text)">lỗi</span>
              </p>
              <p v-if="doc.documentTitle" class="th-text2 text-xs mt-0.5 truncate">{{ doc.documentTitle.replace(/\n/g, ' ') }}</p>
              <p class="th-text3 text-[10px] mt-0.5">
                <span v-if="doc.documentTypeDisplay">{{ doc.documentTypeDisplay }}</span>
                <span v-if="doc.documentNumber"> {{ doc.documentNumber }}</span>
                <span v-if="doc.issuedDate"> · {{ doc.issuedDate }}</span>
              </p>
              <!-- Progress bar for indexing docs -->
              <div v-if="doc.status === 'indexing' && getProgress({ id: 0, type: 'file', label: doc.filename, status: 'uploading', message: '', retries: 0, documentId: doc.id })"
                class="mt-1 w-full th-bg3 rounded-full h-1">
                <div class="th-btn h-1 rounded-full transition-all duration-300"
                  :style="{ width: `${getProgress({ id: 0, type: 'file', label: doc.filename, status: 'uploading', message: '', retries: 0, documentId: doc.id })?.progress ?? 5}%` }" />
              </div>
            </div>
            <span class="w-20 text-right th-text3 text-xs font-mono flex-shrink-0">{{ doc.status === 'indexing' ? '...' : doc.chunkCount }}</span>
            <span class="w-24 text-right th-text3 text-xs flex-shrink-0">{{ formatRelative(doc.createdAt) }}</span>
            <button @click.stop="deleteDoc(doc)" class="w-8 text-center th-text3 hover:text-red-400 text-sm opacity-0 group-hover:opacity-100 flex-shrink-0 transition-opacity">&times;</button>
          </div>
        </div>
      </div>

      <!-- Slide-over document viewer -->
      <Transition name="slide">
        <div v-if="selectedDoc" class="w-[45%] flex-shrink-0 flex flex-col min-w-0 th-elevated border th-border rounded-r-xl overflow-hidden">
          <div class="px-4 py-3 border-b th-border flex items-center justify-between flex-shrink-0">
            <div class="min-w-0">
              <p class="th-text text-sm font-medium truncate">
                {{ selectedDoc.documentTypeDisplay && selectedDoc.documentNumber
                  ? `${selectedDoc.documentTypeDisplay} ${selectedDoc.documentNumber}`
                  : selectedDoc.filename }}
              </p>
              <p v-if="selectedDoc.documentTitle" class="th-text2 text-xs truncate mt-0.5">{{ selectedDoc.documentTitle }}</p>
              <div class="flex items-center gap-2 mt-1">
                <span v-if="selectedDoc.issuedDate" class="th-text3 text-[10px]">{{ selectedDoc.issuedDate }}</span>
                <span class="th-text3 text-[10px]">{{ selectedDoc.chunkCount }} chunks</span>
              </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <div class="flex th-elevated rounded-lg p-0.5">
                <button @click="viewMode = 'markdown'" :class="['px-2.5 py-1 rounded text-xs transition-colors', viewMode === 'markdown' ? 'th-btn text-white' : 'th-text2 hover:th-text']">MD</button>
                <button @click="viewMode = 'chunks'" :class="['px-2.5 py-1 rounded text-xs transition-colors', viewMode === 'chunks' ? 'th-btn text-white' : 'th-text2 hover:th-text']">Chunks</button>
                <button @click="viewMode = 'metadata'" :class="['px-2.5 py-1 rounded text-xs transition-colors', viewMode === 'metadata' ? 'th-btn text-white' : 'th-text2 hover:th-text']">Meta</button>
              </div>
              <button @click="closeViewer" class="w-7 h-7 flex items-center justify-center rounded-lg th-text3 hover:th-text hover:th-bg3 transition-all">&times;</button>
            </div>
          </div>
          <div v-if="contentLoading" class="flex-1 flex items-center justify-center th-text3 text-sm">Đang tải...</div>
          <div v-else-if="viewMode === 'markdown'" class="flex-1 overflow-y-auto p-5">
            <div v-if="markdown" class="prose prose-sm dark:prose-invert max-w-none" style="color: var(--text-primary)" v-html="renderMd(markdown)" />
            <div v-else class="th-text3 text-sm">Không có nội dung</div>
          </div>
          <div v-else-if="viewMode === 'chunks'" class="flex-1 overflow-y-auto">
            <div v-for="(chunk, i) in chunks" :key="chunk.id" class="border-b th-border2">
              <div class="px-4 py-1.5 th-elevated flex items-center justify-between">
                <div class="flex items-center gap-2"><span class="th-accent/70 text-[10px] font-mono">#{{ i }}</span><span v-if="chunk.metadata?.section" class="th-text3 text-xs truncate">{{ chunk.metadata.section }}</span></div>
                <span class="th-text3 text-[10px]">{{ chunk.text.length }} ký tự</span>
              </div>
              <div class="px-4 py-3"><div class="prose prose-sm dark:prose-invert max-w-none" style="color: var(--text-primary)" v-html="renderMd(chunk.text)" /></div>
            </div>
          </div>

          <!-- Metadata editing -->
          <div v-else-if="viewMode === 'metadata'" class="flex-1 flex flex-col overflow-hidden">
            <div class="flex-1 overflow-y-auto p-5 space-y-4">
              <!-- Identity -->
              <fieldset class="space-y-2">
                <legend class="th-text text-xs font-semibold uppercase tracking-wider">Nhận dạng</legend>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="th-text3 text-[10px]">Loại văn bản</label>
                    <select v-model="metaForm.documentType" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm">
                      <option value="">—</option>
                      <option value="thong_tu">Thông tư</option>
                      <option value="nghi_dinh">Nghị định</option>
                      <option value="luat">Luật</option>
                      <option value="quyet_dinh">Quyết định</option>
                      <option value="chi_thi">Chỉ thị</option>
                      <option value="cong_van">Công văn</option>
                    </select>
                  </div>
                  <div>
                    <label class="th-text3 text-[10px]">Tên hiển thị</label>
                    <input v-model="metaForm.documentTypeDisplay" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" placeholder="Thông tư" />
                  </div>
                </div>
                <div>
                  <label class="th-text3 text-[10px]">Số hiệu</label>
                  <input v-model="metaForm.documentNumber" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" placeholder="13/2018/TT-NHNN" />
                </div>
                <div>
                  <label class="th-text3 text-[10px]">Tên văn bản</label>
                  <textarea v-model="metaForm.documentTitle" rows="2" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm resize-none" placeholder="QUY ĐỊNH VỀ..." />
                </div>
              </fieldset>

              <!-- Authority -->
              <fieldset class="space-y-2">
                <legend class="th-text text-xs font-semibold uppercase tracking-wider">Cơ quan ban hành</legend>
                <input v-model="metaForm.issuingAuthority" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" placeholder="Ngân hàng Nhà nước Việt Nam" />
                <input v-model="metaForm.signedLocation" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" placeholder="Hà Nội" />
              </fieldset>

              <!-- Dates -->
              <fieldset class="space-y-2">
                <legend class="th-text text-xs font-semibold uppercase tracking-wider">Ngày tháng</legend>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="th-text3 text-[10px]">Ngày ban hành</label>
                    <input type="date" v-model="metaForm.issuedDate" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" />
                  </div>
                  <div>
                    <label class="th-text3 text-[10px]">Ngày hiệu lực</label>
                    <input type="date" v-model="metaForm.effectiveDate" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" />
                  </div>
                </div>
              </fieldset>

              <!-- Classification -->
              <fieldset class="space-y-2">
                <legend class="th-text text-xs font-semibold uppercase tracking-wider">Phân loại</legend>
                <div>
                  <label class="th-text3 text-[10px]">Lĩnh vực</label>
                  <select v-model="metaForm.domainId" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm">
                    <option :value="0">— Không chọn —</option>
                    <option v-for="d in flatDomains" :key="d.id" :value="d.id">{{ d.name }}</option>
                  </select>
                </div>
                <div>
                  <label class="th-text3 text-[10px]">Tags (phân cách bằng dấu phẩy)</label>
                  <input v-model="metaForm.tags" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm" placeholder="nhnn, thong-tu" />
                </div>
                <div>
                  <label class="th-text3 text-[10px]">Chủ đề (JSON array)</label>
                  <textarea v-model="metaForm.subjects" rows="2" class="w-full th-bg3 border th-border rounded px-2 py-1.5 th-text text-sm resize-none font-mono" placeholder='["Ngân hàng thương mại"]' />
                </div>
              </fieldset>
            </div>

            <!-- Save footer -->
            <div class="px-5 py-3 border-t th-border flex items-center justify-between flex-shrink-0">
              <span v-if="metaMsg" class="text-xs" :class="metaMsg.startsWith('Lỗi') ? 'text-red-500' : 'text-green-500'">{{ metaMsg }}</span>
              <span v-else class="th-text3 text-xs">Thay đổi metadata sẽ tự động cập nhật chunks</span>
              <button @click="saveMetadata" :disabled="metaSaving" class="px-4 py-1.5 th-btn rounded text-white text-sm font-medium disabled:opacity-50">
                {{ metaSaving ? 'Đang lưu...' : 'Lưu metadata' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </div>

    <!-- UPLOAD MODAL -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="modal === 'upload'" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div class="th-elevated border th-border rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl">
            <!-- Sticky header -->
            <div class="px-6 py-4 border-b th-border flex items-center justify-between flex-shrink-0">
              <div>
                <h2 class="text-lg font-semibold th-text">Thêm tài liệu vào "{{ collection }}"</h2>
                <p v-if="queue.length" class="th-text3 text-xs mt-0.5">
                  {{ queue.filter(q => q.status === 'done').length }}/{{ queue.length }} hoàn thành
                  <span v-if="queue.some(q => q.status === 'error')" class="text-red-400"> · {{ queue.filter(q => q.status === 'error').length }} lỗi</span>
                </p>
              </div>
              <button @click="!processing && (modal = null)" :disabled="processing"
                class="w-8 h-8 flex items-center justify-center rounded-lg th-text3 hover:th-text hover:th-bg3 transition-all text-lg disabled:opacity-30">&times;</button>
            </div>

            <!-- Overall progress bar -->
            <div v-if="processing && queue.length" class="flex-shrink-0 px-6 pt-3">
              <div class="w-full th-bg3 rounded-full h-1.5">
                <div class="th-btn h-1.5 rounded-full transition-all duration-500"
                  :style="{ width: `${Math.round(queue.filter(q => q.status === 'done' || q.status === 'error').length / queue.length * 100)}%` }" />
              </div>
            </div>

            <!-- Scrollable content -->
            <div class="flex-1 overflow-y-auto p-6 space-y-4">
              <div @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop"
                :class="['border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors', dragging ? 'th-border th-active' : 'th-border hover:th-border']"
                @click="($refs.fileInput as HTMLInputElement).click()">
                <p class="th-text2 text-sm">Kéo thả file hoặc click để chọn</p>
                <p class="th-text3 text-xs mt-1">PDF, DOCX, XLSX, PPTX, TXT, MD, HTML, CSV</p>
                <input ref="fileInput" type="file" multiple class="hidden" @change="onFileChange" />
              </div>
              <details class="group">
                <summary class="th-text3 text-xs cursor-pointer hover:th-text transition-colors">Nhập nội dung trực tiếp</summary>
                <div class="mt-2 space-y-2">
                  <input v-model="textTitle" placeholder="Tiêu đề" class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none" />
                  <textarea v-model="textContent" placeholder="Nội dung markdown..." rows="4" class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none resize-none font-mono" />
                  <button @click="addText" :disabled="!textContent.trim()" class="px-4 py-1.5 th-bg3 hover:th-bg3 disabled:opacity-50 rounded-lg th-text text-sm transition-colors">+ Thêm</button>
                </div>
              </details>
              <div v-if="queue.length" class="space-y-2 pt-2 border-t th-border">
                <div class="flex items-center justify-between">
                  <p class="th-text2 text-xs font-semibold uppercase">Danh sách ({{ queue.length }})</p>
                  <button v-if="queue.some(q => q.status === 'done' || q.status === 'error')" @click="clearDone" class="text-xs th-text3 hover:th-text">Xoá đã xong</button>
                </div>
                <div v-for="item in queue" :key="item.id" class="th-bg/50 rounded-lg px-3 py-2 group">
                  <div class="flex items-center gap-2">
                    <span class="th-text text-sm truncate flex-1">{{ item.label }}</span>
                    <span :class="['text-xs flex-shrink-0', statusClass(item.status)]">
                      <template v-if="item.status === 'uploading' && getProgress(item)">{{ stageLabel(getProgress(item)!.stage) }}</template>
                      <template v-else-if="item.status === 'done'">{{ item.message }}</template>
                      <template v-else-if="item.status === 'error'">{{ item.message }}</template>
                      <template v-else>chờ</template>
                    </span>
                    <button v-if="item.status === 'pending'" @click="removeQueueItem(item.id)" class="th-text3 hover:text-red-400 text-sm opacity-0 group-hover:opacity-100">&times;</button>
                  </div>
                  <div v-if="item.status === 'uploading'" class="mt-1.5 w-full th-bg3 rounded-full h-1"><div class="th-btn h-1 rounded-full transition-all duration-300" :style="{ width: `${getProgress(item)?.progress ?? 5}%` }" /></div>
                </div>
              </div>
            </div>

            <!-- Sticky footer -->
            <div v-if="queue.length" class="px-6 py-3 border-t th-border flex items-center justify-between flex-shrink-0">
              <span class="th-text3 text-xs">{{ queue.filter(q => q.status === 'done').length }} hoàn thành · {{ queue.filter(q => q.status === 'pending').length }} chờ</span>
              <div class="flex gap-2">
                <button v-if="!processing" @click="modal = null" class="px-4 py-2 th-bg3 rounded-lg th-text2 text-sm transition-colors hover:th-hover">Đóng</button>
                <button @click="startUpload" :disabled="!hasPending || processing" class="px-6 py-2 th-btn disabled:opacity-50 rounded-lg text-white font-medium transition-colors">
                  {{ processing ? (retryRound > 0 ? `Thử lại lần ${retryRound}...` : `Đang xử lý...`) : `Bắt đầu xử lý (${queue.filter(q => q.status === 'pending').length})` }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- COLLECTION SETTINGS MODAL -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="modal === 'settings'" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div class="th-elevated border th-border rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl">
            <!-- Sticky header -->
            <div class="px-6 py-4 border-b th-border flex items-center justify-between flex-shrink-0">
              <h2 class="text-lg font-semibold th-text">Cấu hình collection "{{ collection }}"</h2>
              <div class="flex items-center gap-3">
                <span v-if="saveColMsg" class="text-sm" :class="saveColMsg.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ saveColMsg }}</span>
                <button @click="saveColSettings" :disabled="savingCol" class="px-4 py-2 th-btn hover:th-btn disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">{{ savingCol ? 'Đang lưu...' : 'Lưu' }}</button>
                <button @click="modal = null" class="w-8 h-8 flex items-center justify-center rounded-lg th-text3 hover:th-text hover:th-bg3 text-lg">&times;</button>
              </div>
            </div>
            <!-- Scrollable content -->
            <div class="flex-1 overflow-y-auto p-6 space-y-6">
              <!-- Create / manage -->
              <div class="space-y-3">
                <h3 class="th-text text-sm font-semibold">Quản lý Collections</h3>
                <div class="flex gap-2">
                  <input v-model="newColName" placeholder="Tên mới" class="flex-1 th-bg3 border th-border rounded-lg px-3 py-1.5 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border" />
                  <button @click="createCol" :disabled="creatingCol || !newColName.trim()" class="px-4 py-1.5 th-btn hover:th-btn disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">Tạo</button>
                </div>
                <div v-if="colMessage" class="text-xs" :class="colMessage.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ colMessage }}</div>
                <div class="flex flex-wrap gap-2">
                  <div v-for="c in store.collections" :key="c.name"
                    :class="['flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs', c.name === collection ? 'th-active th-border th-accent' : 'th-bg3 th-border th-text2']">
                    <span class="cursor-pointer" @click="collection = c.name; openSettings()">{{ c.name }}</span>
                    <span class="th-text3">{{ c.documentCount }}</span>
                    <button v-if="c.name !== 'documents'" @click="removeCol(c.name)" class="th-text3 hover:text-red-400 ml-1">&times;</button>
                  </div>
                </div>
              </div>
              <!-- Chunk size -->
              <div class="grid grid-cols-2 gap-6">
                <div class="space-y-2">
                  <div class="flex items-center justify-between"><label class="th-text2 text-xs">Chunk size (tokens)</label><span class="th-accent text-sm font-mono">{{ chunkSize }}</span></div>
                  <input type="range" v-model.number="chunkSize" min="100" max="1000" step="50" class="w-full accent-[var(--accent)]" />
                </div>
                <div class="space-y-2">
                  <div class="flex items-center justify-between"><label class="th-text2 text-xs">Overlap (tokens)</label><span class="th-accent text-sm font-mono">{{ chunkOverlap }}</span></div>
                  <input type="range" v-model.number="chunkOverlap" min="0" max="100" step="5" class="w-full accent-[var(--accent)]" />
                </div>
              </div>
              <!-- Heading -->
              <div class="grid grid-cols-2 gap-6">
                <div class="space-y-2">
                  <div class="flex items-center justify-between"><label class="th-text2 text-xs">Ngưỡng section (tokens)</label><span class="th-accent text-sm font-mono">{{ sectionTokenThreshold }}</span></div>
                  <input type="range" v-model.number="sectionTokenThreshold" min="0" max="2000" step="100" class="w-full accent-[var(--accent)]" />
                </div>
                <div class="flex items-center gap-3 pt-4">
                  <label class="relative inline-flex items-center cursor-pointer"><input type="checkbox" v-model="autoDetectHeadings" class="sr-only peer" /><div class="w-11 h-6 th-bg3 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:th-btn"></div></label>
                  <span class="th-text2 text-sm">Auto-detect heading</span>
                </div>
              </div>
              <!-- Script editor -->
              <div class="space-y-2">
                <h3 class="th-text text-sm font-semibold">Heading script</h3>
                <div ref="editorContainer" class="h-48 rounded-lg overflow-hidden border th-border"></div>
              </div>
              <!-- Test -->
              <div class="space-y-2">
                <h3 class="th-text text-sm font-semibold">Thử nghiệm</h3>
                <textarea v-model="testSampleText" rows="3" placeholder="Dán markdown mẫu..." class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border font-mono resize-y" />
                <button @click="runTest" :disabled="testingChunk || !testSampleText.trim()" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">{{ testingChunk ? 'Đang xử lý...' : 'Thử nghiệm' }}</button>
                <div v-if="testResult" class="space-y-2 mt-2">
                  <p class="th-text text-sm">{{ testResult.chunkCount }} chunks</p>
                  <div v-for="chunk in testResult.chunks" :key="chunk.index" class="th-bg/50 border th-border rounded-lg p-3">
                    <div class="flex items-center justify-between"><span class="th-accent text-xs font-mono">#{{ chunk.index }}</span><span class="th-text3 text-xs">{{ chunk.length }} ký tự</span></div>
                    <pre class="th-text text-xs whitespace-pre-wrap break-words max-h-24 overflow-y-auto mt-1">{{ chunk.text }}</pre>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.slide-enter-active, .slide-leave-active { transition: all 0.2s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; transform: translateX(20px); }
</style>
