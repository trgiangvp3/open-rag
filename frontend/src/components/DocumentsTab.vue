<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { listDocuments, deleteDocument, type DocumentInfo } from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const collection = ref('documents')
const documents = ref<DocumentInfo[]>([])
const loading = ref(false)

async function fetchDocs() {
  loading.value = true
  try {
    const { data } = await listDocuments(collection.value)
    documents.value = data.documents
  } finally {
    loading.value = false
  }
}

async function deleteDoc(doc: DocumentInfo) {
  if (!confirm(`Xóa "${doc.filename}"?`)) return
  await deleteDocument(doc.id, collection.value)
  documents.value = documents.value.filter(d => d.id !== doc.id)
  store.fetch()
}

onMounted(fetchDocs)
watch(collection, fetchDocs)

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('vi-VN')
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center gap-3">
      <select v-model="collection" class="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-slate-300 focus:outline-none focus:border-violet-500">
        <option v-for="c in store.collections" :key="c.name" :value="c.name">{{ c.name }}</option>
      </select>
      <span class="text-slate-500 text-sm">{{ documents.length }} tài liệu</span>
    </div>

    <div v-if="loading" class="text-center text-slate-500 py-12">Đang tải...</div>

    <div v-else-if="documents.length === 0" class="text-center text-slate-600 py-12">
      Chưa có tài liệu nào trong collection này
    </div>

    <div v-else class="space-y-2">
      <div
        v-for="doc in documents"
        :key="doc.id"
        class="flex items-center justify-between bg-slate-800 border border-slate-700 rounded-xl px-4 py-3"
      >
        <div class="flex-1 min-w-0">
          <p class="text-slate-200 font-medium truncate">{{ doc.filename }}</p>
          <p class="text-slate-500 text-xs mt-0.5">{{ doc.chunkCount }} chunks · {{ formatDate(doc.createdAt) }}</p>
        </div>
        <button
          @click="deleteDoc(doc)"
          class="ml-4 text-slate-500 hover:text-red-400 transition-colors text-sm"
        >
          Xóa
        </button>
      </div>
    </div>
  </div>
</template>
