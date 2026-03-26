# OpenRAG — Sơ đồ Kiến trúc & Pipeline

## 1. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER / CLIENT                               │
│                                                                             │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                     Vue 3 + TypeScript (Vite)                        │  │
│   │                                                                      │  │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────┐  │  │
│   │  │ Upload   │  │ Search   │  │ Documents│  │Collections│  │ Chat │  │  │
│   │  │  Tab     │  │  Tab     │  │   Tab    │  │   Tab    │  │ Tab  │  │  │
│   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────┘  │  │
│   │                                                                      │  │
│   │  Axios (REST /api)                    SignalR (/ws/progress)         │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────┬───────────────────────────────────┬───────────────────┘
                      │ HTTP REST                         │ WebSocket
                      ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     .NET 8 API  (http://localhost:8000)                     │
│                                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │Document  │  │ Search   │  │  Chat    │  │Collection│  │   Progress  │  │
│  │Controller│  │Controller│  │Controller│  │Controller│  │     Hub     │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────────────┘  │
│       │              │              │              │              │ SignalR   │
│  ┌────▼─────────────────────────────▼──────────┐  │              │           │
│  │            DocumentService   ChatService    │  │              │           │
│  │            MlClient          LlmClient      │  │              │           │
│  │            CollectionService                │  │              │           │
│  └──────────────────┬───────────────────────────┘  │              │           │
│                     │ HTTP                          │              │           │
│  ┌──────────────────▼────────┐  ┌──────────────────▼────────┐    │           │
│  │     SQLite Database       │  │   OpenAI-compatible LLM   │    │           │
│  │   (Entity Framework Core) │  │   (optional, external)    │    │           │
│  │  Collections / Documents  │  │   gpt-4o-mini / local     │    │           │
│  │  ChatSessions / Messages  │  └───────────────────────────┘    │           │
│  └───────────────────────────┘                                    │           │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ HTTP REST (MlClient)
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  Python ML Service  (http://localhost:8001)                 │
│                          FastAPI + Uvicorn                                  │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Converter   │  │   Embedder   │  │ Hybrid Search│  │   Reranker    │  │
│  │ (MarkItDown) │  │ (bge-m3)     │  │(BM25 + Cosine│  │(bge-reranker) │  │
│  └──────┬───────┘  └──────┬───────┘  │   + RRF)     │  └───────┬───────┘  │
│         │                 │          └──────┬─────────┘          │          │
│  ┌──────▼─────────────────▼────────────────▼────────────────────▼───────┐  │
│  │                    ChromaDB (Persistent Vector Store)                 │  │
│  │                         data/chroma/  (cosine metric)                 │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Stack công nghệ

| Layer | Technology | Chi tiết |
|-------|-----------|---------|
| **Frontend** | Vue 3 + TypeScript | Vite 8, Tailwind CSS 4, Pinia 3, Axios, @microsoft/signalr |
| **Backend API** | .NET 8 Minimal API | EF Core 8 (SQLite), SignalR, SharpToken (cl100k_base), HttpClient |
| **ML Service** | Python + FastAPI | sentence-transformers 3.4, ChromaDB 1.0, FlagEmbedding 1.2, rank-bm25, MarkItDown |
| **Database** | SQLite | File: `data/openrag.db` — metadata, chat history |
| **Vector Store** | ChromaDB | File: `data/chroma/` — embeddings, cosine similarity |
| **LLM** | OpenAI-compatible API | External / local — optional, only for generation |

---

## 3. Pipeline Ingest tài liệu

```
USER
  │
  │  POST /api/documents/upload  (multipart file)
  │  POST /api/documents/text    (plain text)
  ▼
┌─────────────────────────────────────────────────────────────┐
│  .NET DocumentService                                        │
│                                                             │
│  1. Tạo Document record (status = "indexing")               │
│  2. Phát SignalR → progress(10%, "converting")              │
│     │                                                       │
│     ▼  POST /ml/convert (multipart file)                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Python MarkItDown                                  │    │
│  │  PDF / DOCX / XLSX / PPTX / HTML / … → Markdown    │    │
│  └────────────────────────┬────────────────────────────┘    │
│                           │ { markdown: string }            │
│                           ▼                                 │
│  3. Phát SignalR → progress(35%, "chunking")                │
│     │                                                       │
│     ▼  MarkdownChunker.Chunk()  (.NET)                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Semantic Chunker (.NET)                            │    │
│  │  a. Strip metadata sections tiếng Việt             │    │
│  │  b. Split by markdown headers (H1–H6)              │    │
│  │     → header hierarchy làm prefix cho mỗi chunk    │    │
│  │  c. Token-based split (cl100k_base tokenizer)      │    │
│  │     max_tokens = 150, overlap = 20                  │    │
│  │  d. Giữ lại tables & code blocks nguyên vẹn        │    │
│  │  → List<ChunkDto> { text, section, chunk_index }   │    │
│  └────────────────────────┬────────────────────────────┘    │
│                           │                                 │
│  4. Phát SignalR → progress(55%, "embedding")               │
│     │                                                       │
│     ▼  POST /ml/index  { document_id, collection, chunks[] }│
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Python ML Service                                  │    │
│  │  a. BAAI/bge-m3 encode toàn bộ chunks (batch)      │    │
│  │  b. Store vectors + metadata vào ChromaDB          │    │
│  │     collection = tên collection (namespaced)        │    │
│  │     metadata: filename, section, chunk_index        │    │
│  │  → { chunk_count, ok }                              │    │
│  └────────────────────────┬────────────────────────────┘    │
│                           │                                 │
│  5. Phát SignalR → progress(100%, "done")                   │
│  6. Update Document (status="indexed", chunk_count, size)   │
│  7. Return IngestResponse                                   │
└─────────────────────────────────────────────────────────────┘
  │
  ▼ (frontend nhận event SignalR realtime ở mỗi bước)
FRONTEND cập nhật progress bar → hiển thị document trong danh sách
```

---

## 4. Pipeline Search (Hybrid + Rerank + RAG)

```
USER
  │
  │  POST /api/search
  │  { query, collection, top_k, use_reranker, search_mode, generate }
  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  .NET SearchController → MlClient.SearchAsync()                           │
│                                                                           │
│  POST /ml/search → Python ML Service                                      │
│  │                                                                        │
│  ▼  Step 1: Embed query                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  BAAI/bge-m3 encode query → query_vector (1024-dim)               │   │
│  └────────────────────────────────────┬───────────────────────────────┘   │
│                                       │                                   │
│  ▼  Step 2: Retrieve  (theo search_mode)                                  │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  MODE = "semantic"           │  MODE = "hybrid"                   │   │
│  │  ChromaDB cosine search      │  Chạy song song:                   │   │
│  │  → top_k*5 candidates        │  ┌──────────────┐ ┌─────────────┐  │   │
│  │                              │  │ChromaDB cosine│ │ BM25 (lazy │  │   │
│  │                              │  │semantic search│ │  build từ  │  │   │
│  │                              │  │→ top_k*5      │ │ ChromaDB)  │  │   │
│  │                              │  └──────┬────────┘ └──────┬──────┘  │   │
│  │                              │         │                 │          │   │
│  │                              │         └────────┬────────┘          │   │
│  │                              │                  ▼                    │   │
│  │                              │  Reciprocal Rank Fusion (k=60)        │   │
│  │                              │  score = 1/(k+rank_sem)+1/(k+rank_bm25│  │
│  │                              │  → merged top_k*5 candidates          │   │
│  └──────────────────────────────┴──────────────────────────────────────┘   │
│                                       │                                   │
│  ▼  Step 3: Rerank  (nếu use_reranker=true)                               │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  BAAI/bge-reranker-v2-m3  (cross-encoder)                         │   │
│  │  Input: (query, passage) pairs  → rerank_score                    │   │
│  │  Sort by rerank_score DESC → top_k kết quả cuối                   │   │
│  └────────────────────────────────────┬───────────────────────────────┘   │
│                                       │                                   │
│  ▼  Step 4: Generate  (nếu generate=true && LLM configured)               │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  .NET LlmClient → OpenAI-compatible API                           │   │
│  │                                                                    │   │
│  │  System prompt:                                                    │   │
│  │    "Dựa vào các đoạn tài liệu sau, trả lời câu hỏi..."           │   │
│  │                                                                    │   │
│  │  Context: top 8 chunks (đánh số [1]...[8])                        │   │
│  │  User: query                                                       │   │
│  │                                                                    │   │
│  │  → answer (với citation [1], [2], ...) + citations list           │   │
│  └────────────────────────────────────┬───────────────────────────────┘   │
│                                       │                                   │
│  Return SearchResponse:               │                                   │
│  { results[], answer?, citations[] }  │                                   │
└───────────────────────────────────────────────────────────────────────────┘
  │
  ▼
FRONTEND hiển thị:
  - Answer card với citations (nếu có)
  - Danh sách chunks với semantic_score / rerank_score
  - Highlight section & filename nguồn
```

---

## 5. Pipeline Multi-turn Chat (RAG + Session)

```
USER
  │
  │  POST /api/chat
  │  { query, collection, session_id? }
  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  .NET ChatController → ChatService                                        │
│                                                                           │
│  1. Get or Create ChatSession (SQLite)                                    │
│     session_id → lấy session cũ || tạo mới                               │
│                                                                           │
│  2. Retrieve context chunks                                               │
│     MlClient.SearchAsync(query, collection, top_k=5)                     │
│     → hybrid search + rerank (như pipeline Search)                       │
│                                                                           │
│  3. Load conversation history                                             │
│     DB → ChatMessageEntity[]  (role: user|assistant)                     │
│     → List<{ role, content }>                                             │
│                                                                           │
│  4. Generate with history  (LlmClient)                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  Messages gửi LLM:                                                  │ │
│  │                                                                     │ │
│  │  [system]  "Dựa vào tài liệu sau, trả lời câu hỏi.                │ │
│  │            Nếu không có thông tin, hãy nói rõ."                    │ │
│  │                                                                     │ │
│  │  [user]    (tin nhắn cũ 1)                                         │ │
│  │  [assistant] (trả lời cũ 1)                                        │ │
│  │  [user]    (tin nhắn cũ 2)    ← full history từ session            │ │
│  │  [assistant] (trả lời cũ 2)                                        │ │
│  │  …                                                                  │ │
│  │  [user]    "Context:\n[1] chunk1\n[2] chunk2\n\nCâu hỏi: {query}" │ │
│  │                                                                     │ │
│  │  → answer với [1], [2]... citations                                │ │
│  └──────────────────────────────────────────┬──────────────────────────┘ │
│                                             │                             │
│  5. Persist messages (SQLite)               │                             │
│     INSERT ChatMessageEntity (user)         │                             │
│     INSERT ChatMessageEntity (assistant)    │                             │
│     UPDATE ChatSession.UpdatedAt            │                             │
│                                             │                             │
│  Return ChatResponse:                       │                             │
│  { session_id, answer, chunks[], citations }│                             │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
FRONTEND:
  - Lưu session_id vào localStorage
  - Hiển thị bubble chat user/assistant
  - Dropdown "Sources" với chunk previews
  - Click "New chat" → session_id = null → session mới
```

---

## 6. Sơ đồ dữ liệu (Data Stores)

```
┌──────────────────────────────────────────────────────────────────────┐
│                    SQLite  (data/openrag.db)                         │
│                                                                      │
│  collections                     documents                           │
│  ┌──────────────┐                ┌──────────────────────────────┐    │
│  │ id (PK)      │◄───────────────│ id (GUID, PK)                │    │
│  │ name (unique)│  collection_id │ filename                     │    │
│  │ description  │                │ collection_id (FK)           │    │
│  │ created_at   │                │ chunk_count                  │    │
│  └──────────────┘                │ size_bytes                   │    │
│                                  │ status (indexing/indexed/failed)  │
│                                  │ created_at / indexed_at      │    │
│                                  └──────────────────────────────┘    │
│                                                                      │
│  chat_sessions                   chat_messages                      │
│  ┌──────────────┐                ┌──────────────────────────────┐    │
│  │ id (GUID, PK)│◄───────────────│ id (PK)                      │    │
│  │ collection   │  session_id    │ session_id (FK)              │    │
│  │ created_at   │                │ role (user|assistant)        │    │
│  │ updated_at   │                │ content (text)               │    │
│  └──────────────┘                │ created_at                   │    │
│                                  └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                 ChromaDB  (data/chroma/)                             │
│                                                                      │
│  Mỗi Collection → 1 ChromaDB collection (same name)                 │
│                                                                      │
│  document (chunk)                                                    │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ id:        "{document_id}_{chunk_index}"                    │    │
│  │ embedding: float[1024]  (BAAI/bge-m3 output)               │    │
│  │ document:  "chunk text content"                             │    │
│  │ metadata:                                                   │    │
│  │   filename:    "report.pdf"                                 │    │
│  │   section:     "3. Chính sách > 3.1 Nghỉ phép"             │    │
│  │   chunk_index: 7                                            │    │
│  │   document_id: "uuid"                                       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Distance metric: cosine  (L2-normalized inner product)             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 7. API Contract tóm tắt

### Frontend → .NET API  (`http://localhost:8000`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/api/documents/upload` | Upload file → ingest pipeline |
| POST | `/api/documents/text` | Ingest plain text |
| GET | `/api/documents?collection=X` | Danh sách documents |
| DELETE | `/api/documents/{id}?collection=X` | Xóa document + chunks |
| POST | `/api/search` | Tìm kiếm hybrid + rerank + generate |
| POST | `/api/chat` | Chat multi-turn với RAG |
| GET | `/api/chat/{sessionId}/history` | Lịch sử chat |
| DELETE | `/api/chat/{sessionId}` | Xóa session |
| GET | `/api/collections` | Danh sách collections |
| POST | `/api/collections` | Tạo collection |
| DELETE | `/api/collections/{name}` | Xóa collection |
| GET | `/api/health` | System health |
| WS | `/ws/progress` (SignalR) | Real-time progress events |

### .NET API → Python ML Service  (`http://localhost:8001`)

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| POST | `/ml/convert` | File → Markdown (MarkItDown) |
| POST | `/ml/index` | Embed chunks → ChromaDB |
| POST | `/ml/search` | Query → embed → retrieve → rerank |
| POST | `/ml/documents/delete` | Xóa chunks theo document_id |
| POST | `/ml/collections/ensure` | Tạo collection nếu chưa có |
| POST | `/ml/collections/delete` | Xóa collection + tất cả vectors |
| GET | `/ml/health` | Health + model info |

---

## 8. Cấu hình & khởi động

```bash
# Cấu hình .env (copy từ .env.example)
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=auto          # auto | cpu | cuda
RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# LLM (optional — bỏ trống nếu chỉ dùng search)
Llm__BaseUrl=https://api.openai.com/v1
Llm__ApiKey=sk-...
Llm__Model=gpt-4o-mini

# Khởi động ML Service
python ml_service/main_ml.py
# → http://localhost:8001  (load BAAI/bge-m3 ~1.2GB)

# Khởi động .NET API (tự migrate SQLite)
dotnet run -p OpenRAG.Api
# → http://localhost:8000  (serve frontend từ wwwroot/)

# (Dev) Frontend dev server với hot reload
cd frontend && npm run dev
# → http://localhost:5173  (proxy /api, /ws → :8000)
```

---

## 9. Luồng dữ liệu tổng hợp (một cycle đầy đủ)

```
                    ┌──────────┐
                    │   User   │
                    └────┬─────┘
                         │
           ┌─────────────┼──────────────┐
           │ Upload      │ Search/Chat  │
           ▼             ▼              │
    ┌─────────────┐ ┌─────────────┐    │
    │  Vue Upload │ │ Vue Search  │    │
    │     Tab     │ │   /Chat Tab │    │
    └──────┬──────┘ └──────┬──────┘    │
           │               │           │
           ▼               ▼           │
    ┌─────────────────────────────┐    │
    │      .NET 8 API :8000       │    │
    │  ┌────────────────────────┐ │    │
    │  │   DocumentService      │ │    │
    │  │   ChatService          │ │◄───┘
    │  │   MlClient             │ │  WebSocket
    │  │   LlmClient            │ │  (SignalR)
    │  └────────────┬───────────┘ │
    │               │             │    ┌─────────────────┐
    │        ┌──────▼──────┐      │    │ OpenAI-compat   │
    │        │   SQLite    │      │    │ LLM (external)  │
    │        └─────────────┘      │◄───┤ gpt-4o-mini /   │
    └───────────────┬─────────────┘    │ local LLM       │
                    │ HTTP REST        └─────────────────┘
                    ▼
    ┌─────────────────────────────┐
    │   Python ML Service :8001   │
    │                             │
    │  convert → chunk (in .NET)  │
    │  embed → store → search     │
    │  rerank → return results    │
    │                             │
    │  ┌─────────────────────┐   │
    │  │ ChromaDB (vectors)  │   │
    │  │ data/chroma/        │   │
    │  └─────────────────────┘   │
    └─────────────────────────────┘
```
