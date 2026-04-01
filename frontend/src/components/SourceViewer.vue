<script setup lang="ts">
import { computed } from 'vue'
import { type DocumentChunk } from '../api'
import { renderMdPlain, badgeColor } from '../utils/markdown'

const props = defineProps<{
  open: boolean
  title: string
  chunks: DocumentChunk[]
  loading: boolean
  activeIdx: number | null
  highlightId: string | null
  fullMarkdown?: string | null
}>()

defineEmits<{
  close: []
  loadFull: [chunk: DocumentChunk]
}>()

const isFullView = computed(() => !!props.fullMarkdown)

const docMeta = computed(() => {
  const m = props.chunks[0]?.metadata
  if (!m) return { subtitle: '' }
  const title = (m.document_title as string || '').replace(/\n/g, ' ').trim()
  const date = m.issue_date || ''
  const parts: string[] = []
  if (title) parts.push(title)
  if (date) parts.push(date)
  return { subtitle: parts.join(' · ') }
})
</script>

<template>
  <div v-if="open" class="w-[38%] flex-shrink-0 flex flex-col min-w-0 border-l th-border"
    style="background: var(--bg-secondary)">
    <div class="p-3 border-b th-border flex items-center justify-between"
      style="background: var(--bg-elevated)">
      <div class="flex items-center gap-2 min-w-0">
        <span v-if="activeIdx !== null" :class="['inline-flex items-center justify-center w-5 h-5 text-[10px] text-white rounded-full font-bold shadow-sm', badgeColor(activeIdx)]">{{ activeIdx + 1 }}</span>
        <div class="min-w-0">
          <p class="text-sm font-medium truncate" style="color: var(--text-primary)">{{ title }}</p>
          <p v-if="docMeta.subtitle" class="text-[11px] truncate" style="color: var(--text-tertiary)">{{ docMeta.subtitle }}</p>
          <p v-if="!isFullView && chunks.length === 1 && chunks[0].metadata?.section" class="text-xs truncate" style="color: var(--text-accent)">{{ chunks[0].metadata.section }}</p>
        </div>
      </div>
      <div class="flex items-center gap-1.5 flex-shrink-0">
        <button v-if="!isFullView && chunks.length === 1 && chunks[0].metadata?.document_id"
          @click="$emit('loadFull', chunks[0])"
          class="px-2 py-1 text-[10px] rounded border transition-all"
          style="color: var(--text-secondary); background: var(--bg-tertiary); border-color: var(--border-primary)"
          onmouseover="this.style.background='var(--bg-hover)'"
          onmouseout="this.style.background='var(--bg-tertiary)'">
          Xem toàn văn
        </button>
        <span v-if="isFullView" class="text-[10px] px-2 py-1 rounded" style="color: var(--text-accent); background: var(--accent-light)">Toàn văn</span>
        <button @click="$emit('close')"
          class="w-7 h-7 flex items-center justify-center rounded-lg transition-all th-hover"
          style="color: var(--text-tertiary)">&times;</button>
      </div>
    </div>

    <div v-if="loading" class="flex-1 flex items-center justify-center">
      <div class="flex items-center gap-2 text-sm" style="color: var(--text-tertiary)">
        <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Đang tải...
      </div>
    </div>

    <!-- Full markdown view -->
    <div v-else-if="isFullView" class="flex-1 overflow-y-auto">
      <div class="px-6 py-5">
        <div class="prose prose-sm dark:prose-invert max-w-none
          prose-p:my-2 prose-p:leading-relaxed
          prose-headings:font-semibold prose-strong:font-semibold"
          style="color: var(--text-primary)"
          v-html="renderMdPlain(fullMarkdown!)" />
      </div>
    </div>

    <!-- Chunk view -->
    <div v-else class="flex-1 overflow-y-auto">
      <div v-for="(chunk, i) in chunks" :key="chunk.id"
        :id="`viewer-chunk-${chunk.id}`"
        class="border-b th-border2 transition-all"
        :style="highlightId === chunk.id ? 'background: var(--accent-light); border-left: 2px solid var(--accent)' : ''">
        <div v-if="chunks.length > 1" class="px-4 py-1.5 flex items-center gap-2" style="background: var(--bg-overlay)">
          <span class="text-[10px] font-mono" style="color: var(--text-accent); opacity: 0.7">#{{ i }}</span>
          <span v-if="chunk.metadata?.section" class="text-xs truncate" style="color: var(--text-tertiary)">{{ chunk.metadata.section }}</span>
        </div>
        <div class="px-5 py-4">
          <div class="prose prose-sm dark:prose-invert max-w-none
            prose-p:my-2 prose-p:leading-relaxed
            prose-headings:font-semibold prose-strong:font-semibold"
            style="color: var(--text-primary)"
            v-html="renderMdPlain(chunk.text)" />
        </div>
      </div>
    </div>
  </div>
</template>
