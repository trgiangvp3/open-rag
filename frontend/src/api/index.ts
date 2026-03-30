import axios from 'axios'

export const api = axios.create({ baseURL: '/api' })

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
  queryStrategy?: 'direct' | 'multi-query' | 'hyde' | 'multi-query+hyde'
  generate?: boolean
  documentType?: string
  dateFrom?: string
  dateTo?: string
  domainSlug?: string
  subject?: string
}

export interface DomainInfo {
  id: number
  name: string
  slug: string
  children?: DomainInfo[]
}

export interface ChatRequest {
  query: string
  collection?: string
  sessionId?: string
  topK?: number
  useReranker?: boolean
  searchMode?: string
  queryStrategy?: string
}

export interface ChatResponse {
  sessionId: string
  answer?: string
  citations?: number[]
  chunks: ChunkResult[]
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
  api.get(`/documents/${id}/metadata`)

export const updateDocumentTags = (id: string, tags: string | null) =>
  api.put(`/documents/${id}/tags`, { tags })

export const listTags = () =>
  api.get<{ tags: string[] }>('/tags')

export const listDomains = () =>
  api.get<{ domains: DomainInfo[] }>('/domains')

export const setDocumentDomain = (id: string, domainId: number | null) =>
  api.put(`/documents/${id}/domain`, { domainId })

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

// ── Chat ───────────────────────────────────────────────────────────────────

export const chat = (req: ChatRequest) =>
  api.post<ChatResponse>('/chat', req)

export interface ChatHistoryResponse {
  sessionId: string
  messages: { role: string; content: string }[]
}

export const getChatHistory = (sessionId: string) =>
  api.get<ChatHistoryResponse>(`/chat/${sessionId}/history`)

export const deleteChatSession = (sessionId: string) =>
  api.delete(`/chat/${sessionId}`)

// ── Settings ──────────────────────────────────────────────────────────────

export const getSettings = () => api.get<Record<string, string>>('/settings')
export const updateSettings = (settings: Record<string, string>) => api.put<{ ok: boolean }>('/settings', settings)
