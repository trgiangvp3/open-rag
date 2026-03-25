<script setup lang="ts">
import { ref } from 'vue'
import { uploadFile, ingestText } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const collection = ref('documents')

// File upload
const files = ref<{ file: File; status: 'pending' | 'uploading' | 'done' | 'error'; message: string }[]>([])
const dragging = ref(false)

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
  for (const f of newFiles)
    files.value.push({ file: f, status: 'pending', message: '' })
}

async function uploadAll() {
  for (const item of files.value.filter(f => f.status === 'pending')) {
    item.status = 'uploading'
    try {
      const { data } = await uploadFile(item.file, collection.value)
      item.status = 'done'
      item.message = data.message
      store.fetch()
    } catch (e: any) {
      item.status = 'error'
      item.message = e.response?.data?.detail ?? e.message
    }
  }
}

// Text ingest
const textTitle = ref('')
const textContent = ref('')
const textStatus = ref('')
const textLoading = ref(false)

async function submitText() {
  if (!textContent.value.trim()) return
  textLoading.value = true
  textStatus.value = ''
  try {
    const { data } = await ingestText(textContent.value, textTitle.value || 'untitled', collection.value)
    textStatus.value = data.message
    textTitle.value = ''
    textContent.value = ''
    store.fetch()
  } catch (e: any) {
    textStatus.value = 'Lỗi: ' + (e.response?.data?.detail ?? e.message)
  } finally {
    textLoading.value = false
  }
}

const statusClass = (s: string) => ({
  'pending': 'text-slate-400',
  'uploading': 'text-yellow-400 animate-pulse',
  'done': 'text-green-400',
  'error': 'text-red-400',
}[s] ?? 'text-slate-400')
</script>

<template>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
    <!-- File upload -->
    <div class="space-y-4">
      <h3 class="text-slate-300 font-medium">Upload tài liệu</h3>

      <select v-model="collection" class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 focus:outline-none focus:border-violet-500">
        <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
      </select>

      <div
        @dragover.prevent="dragging = true"
        @dragleave="dragging = false"
        @drop.prevent="onDrop"
        :class="['border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors', dragging ? 'border-violet-400 bg-violet-900/20' : 'border-slate-600 hover:border-slate-500']"
        @click="($refs.fileInput as HTMLInputElement).click()"
      >
        <p class="text-slate-400">Kéo thả file hoặc click để chọn</p>
        <p class="text-slate-600 text-sm mt-1">PDF, DOCX, XLSX, PPTX, TXT, MD...</p>
        <input ref="fileInput" type="file" multiple class="hidden" @change="onFileChange" />
      </div>

      <div v-if="files.length" class="space-y-2">
        <div v-for="(f, i) in files" :key="i" class="flex items-center justify-between bg-slate-800 rounded-lg px-3 py-2">
          <span class="text-slate-300 text-sm truncate flex-1">{{ f.file.name }}</span>
          <span :class="['text-xs ml-2', statusClass(f.status)]">
            {{ f.status === 'done' ? f.message : f.status === 'error' ? f.message : f.status }}
          </span>
        </div>
        <button
          @click="uploadAll"
          :disabled="files.every(f => f.status !== 'pending')"
          class="w-full py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
        >
          Upload tất cả
        </button>
      </div>
    </div>

    <!-- Text input -->
    <div class="space-y-4">
      <h3 class="text-slate-300 font-medium">Nhập text trực tiếp</h3>

      <input v-model="textTitle" placeholder="Tiêu đề tài liệu" class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500" />

      <textarea
        v-model="textContent"
        placeholder="Nội dung markdown..."
        rows="10"
        class="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500 resize-none font-mono text-sm"
      />

      <div v-if="textStatus" class="text-sm" :class="textStatus.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ textStatus }}</div>

      <button
        @click="submitText"
        :disabled="textLoading || !textContent.trim()"
        class="w-full py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
      >
        {{ textLoading ? 'Đang xử lý...' : 'Index text' }}
      </button>
    </div>
  </div>
</template>
