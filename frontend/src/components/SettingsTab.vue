<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getSettings, updateSettings } from '../api'

// Categories for left nav
const categories = [
  { id: 'llm', label: 'LLM' },
] as const

const activeCategory = ref<string>('llm')

// LLM settings
const baseUrl = ref('')
const apiKey = ref('')
const model = ref('gpt-4o-mini')
const temperature = ref(0.2)
const maxTokens = ref(2048)
const systemPrompt = ref('')

const showApiKey = ref(false)
const loading = ref(false)
const saving = ref(false)
const saveMsg = ref('')
const testMsg = ref('')
const testing = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await getSettings()
    baseUrl.value = data['Llm:BaseUrl'] ?? ''
    apiKey.value = data['Llm:ApiKey'] ?? ''
    model.value = data['Llm:Model'] ?? 'gpt-4o-mini'
    temperature.value = parseFloat(data['Llm:Temperature'] ?? '0.2')
    maxTokens.value = parseInt(data['Llm:MaxTokens'] ?? '2048', 10)
    systemPrompt.value = data['Llm:SystemPrompt'] ?? ''
  } catch {
    saveMsg.value = 'Lỗi khi tải cài đặt'
  } finally {
    loading.value = false
  }
})

async function save() {
  saving.value = true
  saveMsg.value = ''
  try {
    const payload: Record<string, string> = {
      'Llm:BaseUrl': baseUrl.value,
      'Llm:Model': model.value,
      'Llm:Temperature': temperature.value.toString(),
      'Llm:MaxTokens': maxTokens.value.toString(),
      'Llm:SystemPrompt': systemPrompt.value,
    }
    // Only send API key if it was changed (not masked)
    if (apiKey.value && !apiKey.value.includes('...')) {
      payload['Llm:ApiKey'] = apiKey.value
    }
    await updateSettings(payload)
    saveMsg.value = 'Lưu thành công!'
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } }; message?: string }
    saveMsg.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message)
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testMsg.value = ''
  try {
    await getSettings()
    testMsg.value = 'Kết nối thành công!'
  } catch {
    testMsg.value = 'Kết nối thất bại'
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Category navigation -->
    <aside class="w-72 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Cài đặt</h3>

      <div class="space-y-1.5">
        <div
          v-for="cat in categories"
          :key="cat.id"
          @click="activeCategory = cat.id"
          :class="[
            'bg-slate-800 border rounded-xl px-3 py-2.5 cursor-pointer transition-colors',
            activeCategory === cat.id
              ? 'border-violet-500/50 bg-violet-900/10'
              : 'border-slate-700 hover:border-slate-600'
          ]"
        >
          <p class="text-slate-200 text-sm font-medium">{{ cat.label }}</p>
        </div>
      </div>
    </aside>

    <!-- Right: Settings form -->
    <div class="flex-1 flex flex-col min-w-0 overflow-y-auto">
      <div v-if="loading" class="flex-1 flex items-center justify-center text-slate-600">
        Đang tải cài đặt...
      </div>

      <div v-else-if="activeCategory === 'llm'" class="space-y-6 pb-6">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold text-slate-200">Cấu hình LLM</h2>
          <div class="flex items-center gap-3">
            <span v-if="saveMsg" class="text-sm" :class="saveMsg.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ saveMsg }}</span>
            <button
              @click="save"
              :disabled="saving"
              class="px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
            >
              {{ saving ? 'Đang lưu...' : 'Lưu cài đặt' }}
            </button>
          </div>
        </div>

        <!-- Base URL -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
          <h3 class="text-slate-300 text-sm font-semibold">Kết nối</h3>

          <div class="space-y-3">
            <div class="space-y-1.5">
              <label class="text-slate-400 text-xs">Base URL</label>
              <input
                v-model="baseUrl"
                type="text"
                placeholder="https://api.openai.com/v1"
                class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
              />
              <p class="text-slate-600 text-xs">Địa chỉ API tương thích OpenAI. Để trống nếu dùng OpenAI chính thức.</p>
            </div>

            <div class="space-y-1.5">
              <label class="text-slate-400 text-xs">API Key</label>
              <div class="relative">
                <input
                  v-model="apiKey"
                  :type="showApiKey ? 'text' : 'password'"
                  placeholder="sk-..."
                  class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 pr-16 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
                />
                <button
                  @click="showApiKey = !showApiKey"
                  type="button"
                  class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 text-xs px-2 py-1 transition-colors"
                >
                  {{ showApiKey ? 'Ẩn' : 'Hiện' }}
                </button>
              </div>
              <p class="text-slate-600 text-xs">API key sẽ được ẩn một phần khi hiển thị.</p>
            </div>
          </div>
        </div>

        <!-- Model settings -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
          <h3 class="text-slate-300 text-sm font-semibold">Mô hình</h3>

          <div class="grid grid-cols-2 gap-6">
            <div class="space-y-1.5">
              <label class="text-slate-400 text-xs">Model</label>
              <input
                v-model="model"
                type="text"
                placeholder="gpt-4o-mini"
                class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
              />
            </div>

            <div class="space-y-1.5">
              <label class="text-slate-400 text-xs">Max Tokens</label>
              <input
                v-model.number="maxTokens"
                type="number"
                min="1"
                max="128000"
                class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500"
              />
            </div>
          </div>

          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-slate-400 text-xs">Temperature</label>
              <span class="text-violet-400 text-sm font-mono">{{ temperature }}</span>
            </div>
            <input
              type="range"
              v-model.number="temperature"
              min="0"
              max="2"
              step="0.1"
              class="w-full accent-violet-500"
            />
            <div class="flex justify-between text-slate-600 text-xs">
              <span>0 (chính xác)</span>
              <span>2 (sáng tạo)</span>
            </div>
          </div>
        </div>

        <!-- System Prompt -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
          <h3 class="text-slate-300 text-sm font-semibold">System Prompt</h3>
          <p class="text-slate-500 text-xs">Tuỳ chỉnh prompt hệ thống. Để trống để dùng prompt mặc định.</p>
          <textarea
            v-model="systemPrompt"
            rows="6"
            placeholder="Bạn là trợ lý hữu ích. Trả lời câu hỏi dựa trên ngữ cảnh được cung cấp..."
            class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500 font-mono resize-y"
          ></textarea>
        </div>

        <!-- Test connection -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
          <h3 class="text-slate-300 text-sm font-semibold">Kiểm tra kết nối</h3>
          <p class="text-slate-500 text-xs">Kiểm tra xem cài đặt đã được lưu chính xác hay chưa.</p>
          <div class="flex items-center gap-3">
            <button
              @click="testConnection"
              :disabled="testing"
              class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
            >
              {{ testing ? 'Đang kiểm tra...' : 'Kiểm tra kết nối' }}
            </button>
            <span v-if="testMsg" class="text-sm" :class="testMsg.startsWith('Lỗi') || testMsg.includes('thất bại') ? 'text-red-400' : 'text-green-400'">{{ testMsg }}</span>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>
