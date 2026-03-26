<script setup lang="ts">
import { onMounted, ref, watch, nextTick, onBeforeUnmount, shallowRef } from 'vue'
import { createCollection, deleteCollection, updateCollectionSettings, testHeadingScript, type CollectionInfo, type TestScriptResponse } from '../api'
import { useCollectionsStore } from '../stores/collections'
import * as monaco from 'monaco-editor'

const store = useCollectionsStore()
const newName = ref('')
const newDesc = ref('')
const creating = ref(false)
const message = ref('')

// Settings panel
const selected = ref<CollectionInfo | null>(null)
const saving = ref(false)
const saveMsg = ref('')

// Editable settings
const chunkSize = ref(400)
const chunkOverlap = ref(50)
const sectionTokenThreshold = ref(800)
const autoDetectHeadings = ref(true)
const headingScript = ref<string | null>(null)

// Test panel
const testSampleText = ref('')
const testResult = ref<TestScriptResponse | null>(null)
const testing = ref(false)

// Monaco editor
const editorContainer = ref<HTMLElement | null>(null)
const editorInstance = shallowRef<monaco.editor.IStandaloneCodeEditor | null>(null)

onMounted(store.fetch)

function selectCollection(col: CollectionInfo) {
  selected.value = col
  chunkSize.value = col.chunkSize
  chunkOverlap.value = col.chunkOverlap
  sectionTokenThreshold.value = col.sectionTokenThreshold
  autoDetectHeadings.value = col.autoDetectHeadings
  headingScript.value = col.headingScript
  saveMsg.value = ''
  testResult.value = null

  nextTick(() => {
    initEditor()
  })
}

function initEditor() {
  if (editorInstance.value) {
    editorInstance.value.dispose()
    editorInstance.value = null
  }
  if (!editorContainer.value) return

  const editor = monaco.editor.create(editorContainer.value, {
    value: headingScript.value ?? defaultScript,
    language: 'javascript',
    theme: 'vs-dark',
    minimap: { enabled: false },
    lineNumbers: 'on',
    fontSize: 13,
    tabSize: 2,
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    automaticLayout: true,
    padding: { top: 8, bottom: 8 },
  })

  editor.onDidChangeModelContent(() => {
    const val = editor.getValue()
    headingScript.value = val === defaultScript ? null : val
  })

  editorInstance.value = editor
}

onBeforeUnmount(() => {
  if (editorInstance.value) {
    editorInstance.value.dispose()
  }
})

watch(selected, (val) => {
  if (!val && editorInstance.value) {
    editorInstance.value.dispose()
    editorInstance.value = null
  }
})

const defaultScript = `// detectHeading(line, index, allLines) -> { level, text } | null
// Mẫu: cấu trúc văn bản pháp luật Việt Nam
// Chương > Mục > Điều > Khoản > Điểm
function detectHeading(line, index, allLines) {
  var clean = line.replace(/\\*\\*/g, '').trim();
  if (!clean) return null;

  // Chương — thường đứng riêng 1 dòng, tên chương ở dòng sau
  if (/^Ch[uư][oơ]ng\\s+[IVXLCDM\\d]+/i.test(clean)) {
    var title = clean;
    if (index + 1 < allLines.length) {
      var next = allLines[index + 1].replace(/\\*\\*/g, '').trim();
      // Gộp dòng sau nếu ngắn và không phải heading khác
      if (next && next.length < 100
          && !/^(Ch[uư]|M[uụ]c|[ĐD]i[eề]u|\\d+[.)]|[a-zđ][.)])/i.test(next))
        title = clean + ' — ' + next;
    }
    return { level: 1, text: title };
  }

  // Mục
  if (/^M[uụ]c\\s+\\d+/i.test(clean))
    return { level: 2, text: clean };

  // Điều
  if (/^[ĐD]i[eề]u\\s+\\d+/i.test(clean))
    return { level: 3, text: clean };

  // Khoản — "1." hoặc "1)" đầu dòng
  var k = clean.match(/^(\\d+)[.)]/);
  if (k) return { level: 4, text: 'Khoản ' + k[1] };

  // Điểm — "a)" hoặc "a." đầu dòng (1 chữ cái)
  var d = clean.match(/^([a-zđ])[.)]/);
  if (d) return { level: 5, text: 'Điểm ' + d[1] };

  return null;
}`

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
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } }; message?: string }
    message.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message)
  } finally {
    creating.value = false
  }
}

async function remove(name: string) {
  if (!confirm(`Xoá collection "${name}" và toàn bộ tài liệu?`)) return
  try {
    await deleteCollection(name)
    if (selected.value?.name === name) selected.value = null
    store.fetch()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } }; message?: string }
    message.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message)
  }
}

async function saveSettings() {
  if (!selected.value) return
  saving.value = true
  saveMsg.value = ''
  try {
    await updateCollectionSettings(selected.value.name, {
      chunkSize: chunkSize.value,
      chunkOverlap: chunkOverlap.value,
      sectionTokenThreshold: sectionTokenThreshold.value,
      autoDetectHeadings: autoDetectHeadings.value,
      headingScript: headingScript.value,
    })
    saveMsg.value = 'Lưu thành công!'
    store.fetch()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } }; message?: string }
    saveMsg.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message)
  } finally {
    saving.value = false
  }
}

async function runTest() {
  if (!selected.value || !testSampleText.value.trim()) return
  testing.value = true
  testResult.value = null
  try {
    const scriptVal = headingScript.value ?? undefined
    const { data } = await testHeadingScript(
      selected.value.name,
      scriptVal ?? '',
      testSampleText.value,
      {
        chunkSize: chunkSize.value,
        chunkOverlap: chunkOverlap.value,
        sectionTokenThreshold: sectionTokenThreshold.value,
        autoDetectHeadings: autoDetectHeadings.value,
      }
    )
    testResult.value = data
  } catch (e: unknown) {
    const err = e as { response?: { data?: { message?: string } }; message?: string }
    testResult.value = { status: 'error', chunkCount: 0, chunks: [] }
    saveMsg.value = 'Lỗi: ' + (err.response?.data?.message ?? err.message)
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="flex gap-6 h-[calc(100vh-8rem)]">

    <!-- Left: Collection list + create -->
    <aside class="w-72 flex-shrink-0 space-y-4 overflow-y-auto">
      <h3 class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Collections</h3>

      <!-- Create -->
      <div class="bg-slate-800 border border-slate-700 rounded-xl p-3 space-y-2">
        <h4 class="text-slate-500 text-xs font-semibold uppercase">Tạo collection mới</h4>
        <input v-model="newName" placeholder="Tên collection" class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500" />
        <input v-model="newDesc" placeholder="Mô tả (tuỳ chọn)" class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500" />
        <button @click="create" :disabled="creating" class="w-full py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors">
          Tạo
        </button>
        <div v-if="message" class="text-xs" :class="message.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ message }}</div>
      </div>

      <!-- Collection list -->
      <div class="space-y-1.5">
        <div
          v-for="col in store.collections"
          :key="col.name"
          @click="selectCollection(col)"
          :class="['bg-slate-800 border rounded-xl px-3 py-2.5 cursor-pointer transition-colors group', selected?.name === col.name ? 'border-violet-500/50 bg-violet-900/10' : 'border-slate-700 hover:border-slate-600']"
        >
          <div class="flex items-center justify-between">
            <div class="min-w-0">
              <p class="text-slate-200 text-sm font-medium truncate">{{ col.name }}</p>
              <p class="text-slate-500 text-xs mt-0.5">
                {{ col.documentCount }} tài liệu · {{ col.chunkCount }} chunks
              </p>
              <p v-if="col.description" class="text-slate-600 text-xs mt-0.5 truncate">{{ col.description }}</p>
            </div>
            <button
              v-if="col.name !== 'documents'"
              @click.stop="remove(col.name)"
              class="text-slate-600 hover:text-red-400 transition-colors text-sm opacity-0 group-hover:opacity-100 flex-shrink-0 ml-2"
              title="Xoá collection"
            >&times;</button>
          </div>
        </div>
      </div>
    </aside>

    <!-- Right: Settings panel -->
    <div class="flex-1 flex flex-col min-w-0 overflow-y-auto">
      <div v-if="!selected" class="flex-1 flex items-center justify-center text-slate-600">
        Chọn một collection để xem và chỉnh sửa cấu hình chunking
      </div>

      <div v-else class="space-y-6 pb-6">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-semibold text-slate-200">Cấu hình chunking — {{ selected.name }}</h2>
          <div class="flex items-center gap-3">
            <span v-if="saveMsg" class="text-sm" :class="saveMsg.startsWith('Lỗi') ? 'text-red-400' : 'text-green-400'">{{ saveMsg }}</span>
            <button
              @click="saveSettings"
              :disabled="saving"
              class="px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
            >
              {{ saving ? 'Đang lưu...' : 'Lưu cấu hình' }}
            </button>
          </div>
        </div>

        <!-- Chunk size -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
          <h3 class="text-slate-300 text-sm font-semibold">Kích thước chunk</h3>

          <div class="grid grid-cols-2 gap-6">
            <!-- Chunk size -->
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <label class="text-slate-400 text-xs">Kích thước chunk (tokens)</label>
                <span class="text-violet-400 text-sm font-mono">{{ chunkSize }}</span>
              </div>
              <input type="range" v-model.number="chunkSize" min="100" max="1000" step="50" class="w-full accent-violet-500" />
              <div class="flex justify-between text-slate-600 text-xs">
                <span>100</span>
                <span>1000</span>
              </div>
            </div>

            <!-- Chunk overlap -->
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <label class="text-slate-400 text-xs">Overlap (tokens)</label>
                <span class="text-violet-400 text-sm font-mono">{{ chunkOverlap }}</span>
              </div>
              <input type="range" v-model.number="chunkOverlap" min="0" max="100" step="5" class="w-full accent-violet-500" />
              <div class="flex justify-between text-slate-600 text-xs">
                <span>0</span>
                <span>100</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Heading detection -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-4">
          <h3 class="text-slate-300 text-sm font-semibold">Phát hiện heading</h3>

          <div class="grid grid-cols-2 gap-6">
            <!-- Section token threshold -->
            <div class="space-y-2">
              <div class="flex items-center justify-between">
                <label class="text-slate-400 text-xs">Ngưỡng section (tokens, 0 = tắt)</label>
                <span class="text-violet-400 text-sm font-mono">{{ sectionTokenThreshold }}</span>
              </div>
              <input type="range" v-model.number="sectionTokenThreshold" min="0" max="2000" step="100" class="w-full accent-violet-500" />
              <div class="flex justify-between text-slate-600 text-xs">
                <span>0</span>
                <span>2000</span>
              </div>
            </div>

            <!-- Auto detect headings -->
            <div class="flex items-center gap-3 pt-4">
              <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" v-model="autoDetectHeadings" class="sr-only peer" />
                <div class="w-11 h-6 bg-slate-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-violet-600"></div>
              </label>
              <span class="text-slate-400 text-sm">Tự động phát hiện heading (bold, ALL-CAPS)</span>
            </div>
          </div>
        </div>

        <!-- JS Heading script -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="text-slate-300 text-sm font-semibold">Script phát hiện heading (JavaScript)</h3>
            <span class="text-slate-600 text-xs">Jint engine</span>
          </div>
          <p class="text-slate-500 text-xs">
            Viết hàm <code class="text-violet-400">detectHeading(line, index, allLines)</code> trả về <code class="text-violet-400">{{ '{ level, text }' }}</code> hoặc <code class="text-violet-400">null</code>.
          </p>
          <div ref="editorContainer" class="h-48 rounded-lg overflow-hidden border border-slate-600"></div>
        </div>

        <!-- Test section -->
        <div class="bg-slate-800 border border-slate-700 rounded-xl p-4 space-y-3">
          <h3 class="text-slate-300 text-sm font-semibold">Thử nghiệm</h3>
          <p class="text-slate-500 text-xs">Dán nội dung markdown mẫu để xem kết quả chunking với cấu hình hiện tại.</p>

          <textarea
            v-model="testSampleText"
            rows="6"
            placeholder="Dán nội dung markdown mẫu ở đây..."
            class="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:border-violet-500 font-mono resize-y"
          ></textarea>

          <button
            @click="runTest"
            :disabled="testing || !testSampleText.trim()"
            class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-lg text-white text-sm font-medium transition-colors"
          >
            {{ testing ? 'Đang xử lý...' : 'Thử nghiệm' }}
          </button>

          <!-- Test results -->
          <div v-if="testResult" class="space-y-2 mt-3">
            <p class="text-slate-300 text-sm font-medium">
              Kết quả: <span class="text-violet-400">{{ testResult.chunkCount }}</span> chunks
            </p>
            <div
              v-for="chunk in testResult.chunks"
              :key="chunk.index"
              class="bg-slate-900 border border-slate-700 rounded-lg p-3 space-y-1"
            >
              <div class="flex items-center justify-between">
                <span class="text-violet-400 text-xs font-mono">Chunk #{{ chunk.index }}</span>
                <span class="text-slate-500 text-xs">{{ chunk.length }} ký tự</span>
              </div>
              <p v-if="chunk.metadata?.section" class="text-slate-500 text-xs italic">{{ chunk.metadata.section }}</p>
              <pre class="text-slate-300 text-xs whitespace-pre-wrap break-words max-h-32 overflow-y-auto">{{ chunk.text }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>
