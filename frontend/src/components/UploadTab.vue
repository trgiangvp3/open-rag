<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import * as signalR from '@microsoft/signalr'
import { uploadFile, ingestText, createCollection } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()

// ── Collection selector ─────────────────────────────────────────────────────
const collection = ref('documents')
const newCollectionName = ref('')
const showNewCollection = ref(false)

async function confirmNewCollection() {
  const name = newCollectionName.value.trim()
  if (!name) return
  try {
    await createCollection(name, '')
    await store.fetch()
    collection.value = name
    showNewCollection.value = false
    newCollectionName.value = ''
  } catch {
    collection.value = name
    showNewCollection.value = false
    newCollectionName.value = ''
  }
}

// ── SignalR progress ────────────────────────────────────────────────────────
interface ProgressInfo { stage: string; progress: number }
const progressMap = ref<Map<string, ProgressInfo>>(new Map())

let hubConnection: signalR.HubConnection | null = null
let hubConnected = false

async function ensureHub() {
  if (hubConnected) return
  if (hubConnection) return

  hubConnection = new signalR.HubConnectionBuilder()
    .withUrl('/ws/progress')
    .withAutomaticReconnect([0, 2000, 5000, 10000, 30000]) // 5 retries then stop
    .build()

  hubConnection.on('progress', (event: { documentId: string; stage: string; progress: number }) => {
    progressMap.value = new Map(progressMap.value.set(event.documentId, {
      stage: event.stage,
      progress: event.progress,
    }))
  })

  hubConnection.onclose(() => { hubConnected = false; hubConnection = null })

  try {
    await hubConnection.start()
    hubConnected = true
  } catch {
    hubConnection = null
  }
}

onUnmounted(async () => {
  if (hubConnection) {
    try { await hubConnection.stop() } catch { /* ignore */ }
    hubConnection = null
    hubConnected = false
  }
})

// ── Unified item list ───────────────────────────────────────────────────────
type ItemStatus = 'pending' | 'uploading' | 'done' | 'error'

interface QueueItem {
  id: number
  type: 'file' | 'text'
  label: string
  file?: File
  textTitle?: string
  textContent?: string
  status: ItemStatus
  message: string
  documentId?: string
  retries: number
}

const MAX_RETRIES = 3
let nextId = 0
const queue = ref<QueueItem[]>([])
const dragging = ref(false)
const processing = ref(false)
const retryRound = ref(0)

// Text input form
const textTitle = ref('')
const textContent = ref('')

const hasPending = computed(() => queue.value.some(q => q.status === 'pending'))

function onDrop(e: DragEvent) {
  dragging.value = false
  const dropped = e.dataTransfer?.files
  if (dropped) addFiles(Array.from(dropped))
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addFiles(Array.from(input.files))
  input.value = ''
}

function addFiles(newFiles: File[]) {
  for (const f of newFiles) {
    queue.value.push({
      id: nextId++,
      type: 'file',
      label: f.name,
      file: f,
      status: 'pending',
      message: '',
      retries: 0,
    })
  }
}

function addText() {
  const content = textContent.value.trim()
  if (!content) return
  const title = textTitle.value.trim() || 'untitled'
  queue.value.push({
    id: nextId++,
    type: 'text',
    label: title,
    textTitle: title,
    textContent: content,
    status: 'pending',
    message: '',
    retries: 0,
  })
  textTitle.value = ''
  textContent.value = ''
}

function removeItem(id: number) {
  queue.value = queue.value.filter(q => q.id !== id)
}

function clearDone() {
  queue.value = queue.value.filter(q => q.status !== 'done' && q.status !== 'error')
}

async function processItem(item: QueueItem) {
  item.status = 'uploading'
  item.message = ''
  try {
    if (item.type === 'file' && item.file) {
      const { data } = await uploadFile(item.file, collection.value)
      item.documentId = data.documentId
      item.message = data.message
    } else if (item.type === 'text' && item.textContent) {
      const { data } = await ingestText(item.textContent, item.textTitle || 'untitled', collection.value)
      item.documentId = data.documentId
      item.message = data.message
    }
    item.status = 'done'
  } catch (e: any) {
    item.status = 'error'
    item.retries++
    item.message = e.response?.data?.detail ?? e.message
  }
}

async function startAll() {
  processing.value = true
  retryRound.value = 0
  await ensureHub()

  // Vòng đầu: xử lý tất cả pending
  const pending = queue.value.filter(q => q.status === 'pending')
  for (const item of pending) {
    await processItem(item)
  }

  // Vòng retry: tự động retry các file lỗi
  while (true) {
    const errors = queue.value.filter(q => q.status === 'error' && q.retries < MAX_RETRIES)
    if (errors.length === 0) break

    retryRound.value++
    for (const item of errors) {
      item.message = `Thử lại lần ${item.retries + 1}/${MAX_RETRIES}...`
      await processItem(item)
    }
  }

  // Đánh dấu các file hết retry
  for (const item of queue.value.filter(q => q.status === 'error' && q.retries >= MAX_RETRIES)) {
    item.message = `Thất bại sau ${MAX_RETRIES} lần thử: ${item.message}`
  }

  processing.value = false
  retryRound.value = 0
  store.fetch()
}

function getProgress(item: QueueItem): ProgressInfo | null {
  if (!item.documentId) return null
  return progressMap.value.get(item.documentId) ?? null
}

function stageLabel(stage: string) {
  return { converting: 'Chuyển đổi...', chunking: 'Phân đoạn...', embedding: 'Nhúng...', done: 'Hoàn thành', failed: 'Thất bại' }[stage] ?? stage
}

const statusClass = (s: ItemStatus) => ({
  pending: 'text-slate-400',
  uploading: 'text-yellow-400 animate-pulse',
  done: 'text-green-400',
  error: 'text-red-400',
}[s])

const typeIcon = (t: string) => t === 'file' ? '&#128196;' : '&#128221;'
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Collection + inputs -->
    <aside class="w-80 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Thêm tài liệu</h3>

      <!-- Collection selector -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Collection</label>
        <div class="flex gap-2">
          <select
            v-if="!showNewCollection"
            v-model="collection"
            class="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 text-sm focus:outline-none focus:border-violet-500"
          >
            <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
          </select>
          <input
            v-else
            v-model="newCollectionName"
            placeholder="Tên collection mới..."
            @keyup.enter="confirmNewCollection"
            class="flex-1 bg-slate-800 border border-violet-500 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none"
          />
          <button
            v-if="!showNewCollection"
            @click="showNewCollection = true"
            class="px-2.5 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-xs transition-colors whitespace-nowrap"
          >+ Mới</button>
          <template v-else>
            <button
              @click="confirmNewCollection"
              :disabled="!newCollectionName.trim()"
              class="px-2.5 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white rounded-lg text-xs transition-colors"
            >Tạo</button>
            <button
              @click="showNewCollection = false; newCollectionName = ''"
              class="px-2.5 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-xs transition-colors"
            >Huỷ</button>
          </template>
        </div>
      </div>

      <!-- Drop zone -->
      <div
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onDrop"
        :class="['border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors', dragging ? 'border-violet-400 bg-violet-900/20' : 'border-slate-600 hover:border-slate-500']"
        @click="($refs.fileInput as HTMLInputElement).click()"
      >
        <p class="text-slate-400 text-sm">Kéo thả file hoặc click để chọn</p>
        <p class="text-slate-600 text-xs mt-1">PDF, DOCX, XLSX, PPTX, TXT, MD...</p>
        <input ref="fileInput" type="file" multiple class="hidden" @change="onFileChange" />
      </div>

      <!-- Text input -->
      <div class="space-y-1.5">
        <label class="text-slate-500 text-xs">Hoặc nhập nội dung trực tiếp</label>
        <input
          v-model="textTitle"
          placeholder="Tiêu đề (tuỳ chọn)"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
        />
        <textarea
          v-model="textContent"
          placeholder="Nội dung markdown..."
          rows="5"
          class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500 resize-none font-mono"
        />
        <button
          @click="addText"
          :disabled="!textContent.trim()"
          class="w-full py-1.5 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-lg text-slate-300 text-sm transition-colors"
        >+ Thêm vào danh sách</button>
      </div>
    </aside>

    <!-- Right: Queue -->
    <div class="flex-1 flex flex-col min-w-0">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">
          Danh sách ({{ queue.length }})
        </h3>
        <button
          v-if="queue.some(q => q.status === 'done' || q.status === 'error')"
          @click="clearDone"
          class="text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >Xoá đã xong</button>
      </div>

      <!-- Queue list -->
      <div class="flex-1 overflow-y-auto space-y-1.5">
        <div
          v-for="item in queue"
          :key="item.id"
          class="bg-slate-800 rounded-lg px-3 py-2 group"
        >
          <div class="flex items-center gap-2">
            <span class="text-sm flex-shrink-0" v-html="typeIcon(item.type)" />
            <span class="text-slate-300 text-sm truncate flex-1">{{ item.label }}</span>
            <span :class="['text-xs flex-shrink-0', statusClass(item.status)]">
              <template v-if="item.status === 'uploading' && getProgress(item)">
                {{ stageLabel(getProgress(item)!.stage) }}
              </template>
              <template v-else-if="item.status === 'done'">{{ item.message }}</template>
              <template v-else-if="item.status === 'error'">{{ item.message }}</template>
              <template v-else>chờ xử lý</template>
            </span>
            <button
              v-if="item.status === 'pending'"
              @click="removeItem(item.id)"
              class="text-slate-600 hover:text-red-400 text-sm flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
              title="Xoá"
            >&times;</button>
          </div>
          <div v-if="item.status === 'uploading'" class="mt-1.5 w-full bg-slate-700 rounded-full h-1">
            <div
              class="bg-violet-500 h-1 rounded-full transition-all duration-300"
              :style="{ width: `${getProgress(item)?.progress ?? 5}%` }"
            />
          </div>
        </div>

        <div v-if="!queue.length" class="flex items-center justify-center text-slate-600 h-full text-sm">
          Kéo thả file hoặc nhập nội dung để thêm vào danh sách
        </div>
      </div>

      <!-- Start button -->
      <button
        v-if="queue.length"
        @click="startAll"
        :disabled="!hasPending || processing"
        class="mt-3 w-full py-2.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white font-medium transition-colors"
      >
        {{ processing ? (retryRound > 0 ? `Thử lại lần ${retryRound}...` : 'Đang xử lý...') : 'Bắt đầu xử lý' }}
      </button>
    </div>

  </div>
</template>
