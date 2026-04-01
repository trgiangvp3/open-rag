import axios from 'axios'

export const api = axios.create({ baseURL: '/api' })

export const TOKEN_KEY = 'openrag_token'

// Callback set by auth store to handle logout reactively (avoids circular import)
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

api.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401 && api.defaults.headers.common['Authorization']) {
      // Only clear if we had a token (prevents loop on login attempts)
      localStorage.removeItem(TOKEN_KEY)
      delete api.defaults.headers.common['Authorization']
      onUnauthorized?.()
    }
    return Promise.reject(error)
  }
)

// ── Types ──────────────────────────────────────────────────────────────────

export interface ChunkResult {
  text: string
  score: number
  rerankScore?: number
  metadata: Record<string, string>
}

export interface SearchResponse {
  query: string
  results: ChunkResult[]
  total: number
  answer?: string
  citations?: number[]
}

export interface DocumentInfo {
  id: string
  filename: string
  collection: string
  chunkCount: number
  createdAt: string
  documentType?: string
  documentTypeDisplay?: string
  documentNumber?: string
  documentTitle?: string
  issuingAuthority?: string
  issuedDate?: string
  tags?: string
  status?: string // "indexed" | "indexing" | "failed"
}

export interface DocumentListResponse {
  documents: DocumentInfo[]
  total: number
}

export interface CollectionInfo {
  name: string
  description: string
  documentCount: number
  chunkCount: number
  chunkSize: number
  chunkOverlap: number
  sectionTokenThreshold: number
  autoDetectHeadings: boolean
  headingScript: string | null
}

export interface CollectionSettings {
  chunkSize: number
  chunkOverlap: number
  sectionTokenThreshold: number
  autoDetectHeadings: boolean
  headingScript: string | null
}

export interface TestScriptResponse {
  status: string
  chunkCount: number
  chunks: { index: number; text: string; metadata: Record<string, string>; length: number }[]
}

export interface IngestResponse {
  documentId: string
  filename: string
  chunkCount: number
  message: string
}

export interface StatusResponse {
  status: string
  message: string
}

export interface SearchOptions {
  useReranker?: boolean
  searchMode?: 'semantic' | 'hybrid'
  generate?: boolean
  documentType?: string
  dateFrom?: string
  dateTo?: string
  tags?: string
  scoreThreshold?: number
  domainSlug?: string
  subject?: string
}

export interface DomainInfo {
  id: number
  name: string
  slug: string
  children?: DomainInfo[]
}

// ── Search ─────────────────────────────────────────────────────────────────

export const search = (query: string, collection: string, topK: number, opts: SearchOptions = {}) =>
  api.post<SearchResponse>('/search', { query, collection, topK, ...opts })

// ── Documents ──────────────────────────────────────────────────────────────

export const uploadFile = (file: File, collection: string, tags?: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('collection', collection)
  if (tags) form.append('tags', tags)
  return api.post<IngestResponse>('/documents/upload', form)
}

export const ingestText = (text: string, title: string, collection: string) => {
  const form = new FormData()
  form.append('text', text)
  form.append('title', title)
  form.append('collection', collection)
  return api.post<IngestResponse>('/documents/text', form)
}

export const listDocuments = (collection: string) =>
  api.get<DocumentListResponse>('/documents', { params: { collection } })

export const deleteDocument = (id: string, collection: string) =>
  api.delete<StatusResponse>(`/documents/${id}`, { params: { collection } })

export interface DocumentChunk {
  id: string
  text: string
  metadata: Record<string, string>
}

export interface DocumentChunksResponse {
  document_id: string
  chunks: DocumentChunk[]
  total: number
}

export const getDocumentChunks = (id: string, collection: string) =>
  api.get<DocumentChunksResponse>(`/documents/${id}/chunks`, { params: { collection } })

export interface DocumentMarkdownResponse {
  documentId: string
  filename: string
  markdown: string
}

export const getDocumentMarkdown = (id: string) =>
  api.get<DocumentMarkdownResponse>(`/documents/${id}/markdown`)

export const getDocumentMetadata = (id: string) =>
  api.get<Record<string, unknown>>(`/documents/${id}/metadata`)

export const updateDocumentMetadata = (id: string, data: Record<string, unknown>) =>
  api.patch<{ status: string; chunksUpdated: number }>(`/documents/${id}/metadata`, data)

export const listDomains = () =>
  api.get<{ domains: DomainInfo[] }>('/domains')

// ── Collections ────────────────────────────────────────────────────────────

export const listCollections = () =>
  api.get<CollectionInfo[]>('/collections')

export const createCollection = (name: string, description: string) =>
  api.post<StatusResponse>('/collections', { name, description })

export const deleteCollection = (name: string) =>
  api.delete<StatusResponse>(`/collections/${name}`)

export const updateCollectionSettings = (name: string, settings: Partial<CollectionSettings>) =>
  api.put<StatusResponse>(`/collections/${name}/settings`, settings)

export const testHeadingScript = (name: string, script: string, sampleText: string, settings?: Partial<CollectionSettings>) =>
  api.post<TestScriptResponse>(`/collections/${name}/test-heading-script`, { script, sampleText, ...settings })

// ── Health ─────────────────────────────────────────────────────────────────

export const health = () =>
  api.get<{ status: string }>('/health')

// ── Settings ──────────────────────────────────────────────────────────────

export const getSettings = () => api.get<Record<string, string>>('/settings')
export const updateSettings = (settings: Record<string, string>) => api.put<{ ok: boolean }>('/settings', settings)
