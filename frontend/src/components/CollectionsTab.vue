<script setup lang="ts">
import { onMounted } from 'vue'
import { createCollection, deleteCollection } from '../api'
import { useCollectionsStore } from '../stores/collections'
import { ref } from 'vue'

const store = useCollectionsStore()
const newName = ref('')
const newDesc = ref('')
const creating = ref(false)
const message = ref('')

onMounted(store.fetch)

async function create() {
  if (!newName.value.trim()) return
  creating.value = true
  message.value = ''
  try {
    await createCollection(newName.value.trim(), newDesc.value.trim())
    message.value = `Đã tạo collection "${newName.value}"`
    newName.value = ''
    newDesc.value = ''
    store.fetch()
  } catch (e: any) {
    message.value = 'Lỗi: ' + (e.response?.data?.message ?? e.message)
  } finally {
    creating.value = false
  }
}

async function remove(name: string) {
  if (!confirm(`Xóa collection "${name}" và toàn bộ tài liệu?`)) return
  await deleteCollection(name)
  store.fetch()
}
</script>

<template>
  <div class="space-y-6">
    <!-- Create -->
    <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
      <h3 class="text-slate-300 font-medium">Tạo collection mới</h3>
      <div class="flex gap-2">
        <input v-model="newName" placeholder="Tên collection" class="flex-1 bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500" />
        <input v-model="newDesc" placeholder="Mô tả (tuỳ chọn)" class="flex-1 bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-violet-500" />
        <button @click="create" :disabled="creating" class="px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">
          Tạo
        </button>
      </div>
      <div v-if="message" class="text-sm" :class="message.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ message }}</div>
    </div>

    <!-- List -->
    <div class="space-y-2">
      <div
        v-for="col in store.collections"
        :key="col.name"
        class="flex items-center justify-between bg-slate-800 border border-slate-700 rounded-xl px-4 py-3"
      >
        <div>
          <p class="text-slate-200 font-medium">{{ col.name }}</p>
          <p class="text-slate-500 text-xs mt-0.5">
            {{ col.documentCount }} tài liệu · {{ col.chunkCount }} chunks
            <span v-if="col.description" class="ml-2 text-slate-600">— {{ col.description }}</span>
          </p>
        </div>
        <button
          v-if="col.name !== 'documents'"
          @click="remove(col.name)"
          class="text-slate-500 hover:text-red-400 transition-colors text-sm"
        >
          Xóa
        </button>
      </div>
    </div>
  </div>
</template>
