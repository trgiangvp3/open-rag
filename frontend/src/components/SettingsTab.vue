<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getSettings, updateSettings } from '../api'
import { useSearchSettingsStore } from '../stores/searchSettings'

const categories = [
  { id: 'search', label: 'Tìm kiếm' },
  { id: 'llm', label: 'LLM' },
] as const

const activeCategory = ref<string>('search')
const searchStore = useSearchSettingsStore()

const DEFAULT_SYSTEM_PROMPT = `Bạn là trợ lý tra cứu văn bản quy phạm pháp luật Việt Nam. Trả lời câu hỏi dựa trên ngữ cảnh được cung cấp.

Quy tắc:
- Trả lời bằng tiếng Việt, chính xác và ngắn gọn
- Trích dẫn cụ thể điều, khoản, điểm khi có thể (VD: "Theo Điều 5 Khoản 2...")
- Ghi rõ tên văn bản, số hiệu khi đề cập (VD: "Thông tư 39/2016/TT-NHNN")
- Chỉ trả lời dựa trên ngữ cảnh được cung cấp, không suy đoán
- Nếu ngữ cảnh không đủ thông tin, nói rõ "Không tìm thấy thông tin liên quan trong tài liệu"
- Sử dụng trích dẫn [1], [2]... để tham chiếu đến nguồn`

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
    systemPrompt.value = data['Llm:SystemPrompt'] || DEFAULT_SYSTEM_PROMPT
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
    testMsg.value = 'API kết nối thành công!'
  } catch {
    testMsg.value = 'Kết nối API thất bại'
  } finally {
    testing.value = false
  }
}

function resetPrompt() {
  systemPrompt.value = DEFAULT_SYSTEM_PROMPT
}
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Category navigation -->
    <aside class="w-72 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="th-text2 text-xs font-semibold uppercase tracking-wider">Cài đặt</h3>

      <div class="space-y-1.5">
        <div
          v-for="cat in categories"
          :key="cat.id"
          @click="activeCategory = cat.id"
          :class="[
            'th-elevated border rounded-xl px-3 py-2.5 cursor-pointer transition-colors',
            activeCategory === cat.id
              ? 'th-border th-active'
              : 'th-border hover:th-border'
          ]"
        >
          <p class="th-text text-sm font-medium">{{ cat.label }}</p>
        </div>
      </div>
    </aside>

    <!-- Right: Settings form -->
    <div class="flex-1 flex flex-col min-w-0 overflow-y-auto">

      <!-- Search settings -->
      <div v-if="activeCategory === 'search'" class="space-y-6 pb-6">
        <h2 class="text-lg font-semibold th-text">Cài đặt tìm kiếm</h2>
        <p class="th-text3 text-sm -mt-4">Cấu hình mặc định cho chế độ tìm kiếm AI. Các thay đổi được lưu tự động.</p>

        <!-- TopK -->
        <div class="th-elevated border th-border rounded-xl p-4 space-y-4">
          <h3 class="th-text text-sm font-semibold">Chung</h3>

          <div class="space-y-1.5">
            <label class="th-text2 text-xs">Số kết quả (Top-K)</label>
            <select v-model="searchStore.topK"
              class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none focus:th-border">
              <option v-for="n in [3, 5, 10, 15, 20]" :key="n" :value="n">Top {{ n }}</option>
            </select>
            <p class="th-text3 text-xs">Số lượng kết quả trả về cho mỗi tìm kiếm.</p>
          </div>
        </div>

        <!-- Retrieval settings -->
        <div class="th-elevated border th-border rounded-xl p-4 space-y-4">
          <h3 class="th-text text-sm font-semibold">Phương pháp truy xuất</h3>

          <div class="space-y-1.5">
            <label class="th-text2 text-xs">Retrieval Mode</label>
            <select v-model="searchStore.retrievalMode"
              class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text text-sm focus:outline-none focus:th-border">
              <option value="semantic">Semantic</option>
              <option value="hybrid">Hybrid</option>
            </select>
            <p class="th-text3 text-xs">Semantic dùng embedding, Hybrid kết hợp BM25 + embedding.</p>
          </div>

          <label class="flex items-center gap-3 cursor-pointer select-none mt-2">
            <input type="checkbox" v-model="searchStore.useReranker" class="accent-[var(--accent)] rounded w-4 h-4" />
            <div>
              <span class="th-text text-sm">Sử dụng Reranker</span>
              <p class="th-text3 text-xs">Xếp hạng lại kết quả bằng mô hình cross-encoder để tăng độ chính xác.</p>
            </div>
          </label>
        </div>

        <!-- Score Threshold -->
        <div class="th-elevated border th-border rounded-xl p-4 space-y-4">
          <h3 class="th-text text-sm font-semibold">Lọc kết quả</h3>

          <div class="space-y-1.5">
            <label class="th-text2 text-xs">Score Threshold</label>
            <div class="flex items-center gap-3">
              <input type="range" min="0" max="1" step="0.05"
                :value="searchStore.scoreThreshold ?? 0"
                @input="searchStore.scoreThreshold = Number(($event.target as HTMLInputElement).value) || null"
                class="flex-1 accent-[var(--accent)]" />
              <span class="th-text text-sm font-mono w-10 text-right">{{ searchStore.scoreThreshold ?? 'Off' }}</span>
            </div>
            <p class="th-text3 text-xs">Lọc bỏ kết quả có điểm thấp hơn ngưỡng này. Đặt 0 để tắt.</p>
          </div>
        </div>
      </div>

      <!-- LLM settings -->
      <div v-else-if="activeCategory === 'llm'" class="space-y-6 pb-6">
        <div v-if="loading" class="flex-1 flex items-center justify-center th-text3">
          Đang tải cài đặt...
        </div>

        <template v-else>
          <div class="flex items-center justify-between">
            <h2 class="text-lg font-semibold th-text">Cấu hình LLM</h2>
            <div class="flex items-center gap-3">
              <span v-if="saveMsg" class="text-sm" :class="saveMsg.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ saveMsg }}</span>
              <button
                @click="save"
                :disabled="saving"
                class="px-4 py-2 th-btn hover:th-btn disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
              >
                {{ saving ? 'Đang lưu...' : 'Lưu cài đặt' }}
              </button>
            </div>
          </div>

          <!-- Base URL -->
          <div class="th-elevated border th-border rounded-xl p-4 space-y-4">
            <h3 class="th-text text-sm font-semibold">Kết nối</h3>
            <div class="space-y-3">
              <div class="space-y-1.5">
                <label class="th-text2 text-xs">Base URL</label>
                <input v-model="baseUrl" type="text" placeholder="https://api.openai.com/v1"
                  class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border" />
                <p class="th-text3 text-xs">Địa chỉ API tương thích OpenAI. Để trống nếu dùng OpenAI chính thức.</p>
              </div>
              <div class="space-y-1.5">
                <label class="th-text2 text-xs">API Key</label>
                <div class="relative">
                  <input v-model="apiKey" :type="showApiKey ? 'text' : 'password'" placeholder="sk-..."
                    class="w-full th-bg3 border th-border rounded-lg px-3 py-2 pr-16 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border" />
                  <button @click="showApiKey = !showApiKey" type="button"
                    class="absolute right-2 top-1/2 -translate-y-1/2 th-text3 hover:th-text text-xs px-2 py-1 transition-colors">
                    {{ showApiKey ? 'Ẩn' : 'Hiện' }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Model settings -->
          <div class="th-elevated border th-border rounded-xl p-4 space-y-4">
            <h3 class="th-text text-sm font-semibold">Mô hình</h3>
            <div class="grid grid-cols-2 gap-6">
              <div class="space-y-1.5">
                <label class="th-text2 text-xs">Model</label>
                <input v-model="model" type="text" placeholder="gpt-4o-mini"
                  class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border" />
              </div>
              <div class="space-y-1.5">
                <label class="th-text2 text-xs">Max Tokens</label>
                <input v-model.number="maxTokens" type="number" min="1" max="128000"
                  class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border" />
              </div>
            </div>
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <label class="th-text2 text-xs">Temperature</label>
                <span class="th-accent text-sm font-mono">{{ temperature }}</span>
              </div>
              <input type="range" v-model.number="temperature" min="0" max="2" step="0.1" class="w-full accent-[var(--accent)]" />
              <div class="flex justify-between th-text3 text-xs">
                <span>0 (chính xác)</span>
                <span>2 (sáng tạo)</span>
              </div>
            </div>
          </div>

          <!-- System Prompt -->
          <div class="th-elevated border th-border rounded-xl p-4 space-y-3">
            <div class="flex items-center justify-between">
              <h3 class="th-text text-sm font-semibold">System Prompt</h3>
              <button @click="resetPrompt" class="th-text3 hover:th-text text-xs transition-colors">Khôi phục mặc định</button>
            </div>
            <textarea
              v-model="systemPrompt"
              rows="10"
              class="w-full th-bg3 border th-border rounded-lg px-3 py-2 th-text placeholder:th-text3 text-sm focus:outline-none focus:th-border font-mono resize-y"
            ></textarea>
          </div>

          <!-- Test connection -->
          <div class="th-elevated border th-border rounded-xl p-4 space-y-3">
            <h3 class="th-text text-sm font-semibold">Kiểm tra kết nối</h3>
            <div class="flex items-center gap-3">
              <button @click="testConnection" :disabled="testing"
                class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">
                {{ testing ? 'Đang kiểm tra...' : 'Kiểm tra kết nối' }}
              </button>
              <span v-if="testMsg" class="text-sm" :class="testMsg.includes('thất bại') ? 'text-red-400' : 'text-green-400'">{{ testMsg }}</span>
            </div>
          </div>
        </template>
      </div>
    </div>

  </div>
</template>
