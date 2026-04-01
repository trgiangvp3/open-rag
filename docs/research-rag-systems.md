# Research: Quản lý tài liệu & Tham số tìm kiếm trong các hệ thống RAG

> Ngày nghiên cứu: 2026-04-01

## 1. Document Management

### 1.1 Chunking Strategies

| Chiến lược | Mô tả | Khi nào dùng |
|---|---|---|
| **Fixed-size splitting** | Cắt theo số token cố định (256-512 tokens) | Dữ liệu đồng nhất, đơn giản |
| **Recursive character splitting** | Cắt theo thứ tự ưu tiên: paragraph > sentence > word | **Mặc định tốt nhất** - benchmark 2026 đạt 69% accuracy |
| **Semantic chunking** | Dùng embeddings để phát hiện ranh giới chủ đề | Tài liệu dài, đa chủ đề |
| **Document-aware / Layout-aware** | Nhận biết cấu trúc: heading, bảng, danh sách | PDF phức tạp, tài liệu pháp lý |
| **Contextual retrieval** (mới 2026) | Thêm context tóm tắt vào mỗi chunk | Cải thiện retrieval accuracy đáng kể |
| **Late chunking** (mới 2026) | Embed toàn bộ document trước, chunk sau | Giữ context toàn cục |

**Thông số khuyến nghị:**
- Chunk size: **256-512 tokens** (mặc định tốt nhất)
- Overlap: **10-20%** (50-100 tokens cho chunk 512 token)
- Nghiên cứu tháng 1/2026: overlap **không cải thiện recall** khi dùng SPLADE retrieval

### 1.2 Document Lifecycle

| Hệ thống | Tổ chức | CRUD | Versioning |
|---|---|---|---|
| **Pinecone** | Indexes + Namespaces (tối đa 100K namespaces/index) | Upsert, Delete by ID/filter | Không native; dùng metadata field |
| **Qdrant** | Collections + Points | Full CRUD: upsert, delete, update payload | Không native; dùng version field |
| **Weaviate** | Classes (schema-based) + Tenants | Full CRUD với REST/GraphQL | Không native; dùng metadata |
| **Milvus** | Collections + Partitions | Insert, Delete, Upsert | Không native |
| **Vectara** | Corpora + Documents | Upload, replace, delete documents | Document-level replacement |
| **LlamaIndex** | In-memory indexes | Insert, refresh, delete nodes | Không built-in |
| **LangChain** | VectorStore abstraction | add_documents, delete | Phụ thuộc vector store backend |
| **Haystack** | DocumentStore abstraction | write, update, delete | Phụ thuộc backend |

### 1.3 Document Organization & Multi-tenancy

| Hệ thống | Cấu trúc phân cấp | Multi-tenancy |
|---|---|---|
| **Pinecone** | Index > Namespace | Namespace-based isolation |
| **Qdrant** | Collection > Points (with payload) | First-class: named tenants, quota controls, sharding |
| **Weaviate** | Class > Objects | Tenant-aware classes, ACLs, dedicated shards |
| **Milvus** | Database > Collection > Partition | Partition-based hoặc database-level isolation |
| **Vectara** | Account > Corpus > Document | Corpus-level isolation |

### 1.4 Metadata phổ biến trong production

- **source** - nguồn tài liệu (URL, file path)
- **title** - tiêu đề document
- **author / owner** - tác giả
- **created_at / updated_at** - timestamp
- **document_id** - ID gốc (liên kết chunks)
- **chunk_index** - thứ tự chunk trong document
- **page_number** - số trang (PDF)
- **file_type** - loại file
- **language** - ngôn ngữ
- **sensitivity / access_level** - mức độ bảo mật
- **effective_date** - ngày hiệu lực
- **tags / categories** - nhãn phân loại
- **version** - phiên bản

---

## 2. Search Parameters

### 2.1 Similarity Metrics

| Metric | Mô tả | Khi nào dùng |
|---|---|---|
| **Cosine similarity** | Đo góc giữa vectors | **Mặc định phổ biến nhất** cho text embeddings |
| **Dot product** | Tích vô hướng, xét cả magnitude | Khi embeddings đã normalized |
| **Euclidean (L2)** | Khoảng cách tuyệt đối | Clustering |

### 2.2 Retrieval Parameters

| Tham số | Mô tả | Giá trị thường dùng |
|---|---|---|
| **top_k** | Số kết quả trả về | 3-10 (RAG), 20-100 (search UI) |
| **score_threshold** | Ngưỡng điểm tối thiểu | 0.5-0.8 (cosine) |
| **offset / limit** | Phân trang | Tùy UI |
| **metadata_filter** | Lọc theo metadata | $eq, $ne, $gt, $lt, $in, $nin, boolean logic |

### 2.3 Hybrid Search

| Thành phần | Chi tiết |
|---|---|
| **Keyword** | BM25/BM25F. Params: k1 (term saturation, mặc định 1.2), b (length norm, mặc định 0.75) |
| **Vector** | Semantic embedding + ANN (HNSW) |
| **Fusion** | **RRF** (phổ biến nhất) hoặc **Weighted scoring** với alpha (0=keyword, 1=vector) |

### 2.4 Reranking

| Phương pháp | Mô tả | Hiệu quả |
|---|---|---|
| **Cross-encoder** | Score lại từng cặp query-document | +15-30% precision |
| **Cohere / Jina Reranker** | API thương mại | Dễ tích hợp |
| **MMR** | Cân bằng relevance vs diversity (lambda 0-1) | Giảm redundancy |

**Pipeline khuyến nghị:** Bi-encoder (top 50-100) -> Reranker (top 5-10) -> LLM

### 2.5 Search Modes

| Mode | Hệ thống hỗ trợ |
|---|---|
| **Semantic (vector-only)** | Tất cả |
| **Keyword (BM25-only)** | Weaviate, Haystack, Vectara, Elasticsearch |
| **Hybrid** | Weaviate, Qdrant, Pinecone, Vectara, Milvus 2.4+ |
| **Filtered search** | Tất cả (pre-filter hoặc post-filter) |
| **Multi-vector / ColBERT** | Qdrant, Vespa |

---

## 3. Best Practices cho Production RAG

### 3.1 Document Deduplication
- Content hash (MD5/SHA256) để phát hiện exact duplicates
- Semantic deduplication (embedding similarity > 0.95) cho near-duplicates
- Dedup ở cấp document trước khi chunk

### 3.2 Access Control
- Tránh mô hình "one big bucket"
- Document-level ACLs trong retriever
- Namespace/tenant isolation cho B2B
- Metadata field `access_level` / `tenant_id`

### 3.3 Search Quality Tuning
- 60% RAG deployments mới (2026) tích hợp evaluation từ đầu
- Metrics từng stage: retrieval precision, reranker accuracy, generation faithfulness
- Hybrid search + reranking cải thiện precision 15-30%
- Frameworks: RAGAS, Evidently, TruLens

---

## 4. Đánh giá tự phê: Query Expansion & Domain Taxonomy có thực sự hiệu quả?

> Cập nhật: 2026-04-01. Sau khi nghiên cứu langflow-ai/openrag và các nguồn học thuật,
> kết luận rằng một số hướng đi hiện tại của open-rag **không hiệu quả bằng cách làm
> của các hệ thống production thực tế**.

### 4.1 Query Expansion (Multi-Query, HyDE) - KHÔNG hiệu quả như kỳ vọng

**Bằng chứng chống lại:**

| Nguồn | Phát hiện |
|---|---|
| **RAGO (ISCA 2025)** | Query rewriter tăng TTFT latency **2.4x**, trong khi reranking chỉ ảnh hưởng tối thiểu |
| **ZenML (production)** | Expanded queries bị **query drift** - thay đổi ý nghĩa câu hỏi, user nhận câu trả lời cho câu hỏi họ chưa bao giờ hỏi |
| **Medical QA (ACM 2025)** | Negative impact (6.37-6.84%) **vượt qua** positive impact (5.11-6.05%) khi thêm retrieval layers |
| **HyDE + legal domain** | LLM sinh tài liệu giả định chứa điều khoản **không tồn tại** - hallucination trực tiếp hại retrieval |

**Tại sao langflow-ai/openrag KHÔNG implement query expansion?**

Vì **hybrid search (BM25 + vector) đã giải quyết cùng vấn đề**, rẻ hơn và an toàn hơn:
- BM25 bắt exact match (số hiệu, tên riêng) mà vector search bỏ sót
- Vector search bắt semantic similarity mà BM25 bỏ sót
- Kết hợp 2 cái = đã cover ~80-90% giá trị mà query expansion cố mang lại
- Thêm prefix fallback (25% weight) để xử lý partial input

**Thứ tự ROI thực tế (giảm dần):**

| # | Kỹ thuật | Impact | Chi phí | open-rag? |
|---|---|---|---|---|
| 1 | Chunking tốt | Rất cao | Thấp | Có |
| 2 | Hybrid search (BM25 + vector) | **+48%** quality | Trung bình | Có |
| 3 | Reranker (cross-encoder) | **+48%** precision | Thấp (~100ms) | Có |
| 4 | Metadata filtering + facets | Cao | Thấp | **Thiếu** |
| 5 | Score threshold | Trung bình | Rất thấp | **Thiếu** |
| 6 | Rule-based query normalization | Trung bình | Thấp | **Thiếu** |
| 7 | Multi-Query expansion | +20-30% recall, **có rủi ro** | Cao (300-1000ms) | Có (nên xem lại) |
| 8 | HyDE | Thấp, **rủi ro cao** | Cao | Có (nên xem lại) |

> **Kết luận:** open-rag đã implement #1-3 (tốt), nhưng đầu tư vào #7-8 trong khi chưa
> tối ưu #4-6. langflow-ai/openrag tập trung vào #1-5 rồi dừng lại - đó là quyết định đúng.

### 4.2 Domain Taxonomy & Soft Boost - CHƯA CHỨNG MINH hiệu quả

**Vấn đề với domain boost hiện tại:**

```
DomainL2Boost = 0.15   // khá lớn - có thể tạo bias
DomainL1Boost = 0.08
SubjectBoost  = 0.07
```

- Boost 0.15 có thể đẩy kết quả **không thực sự liên quan** lên trên chỉ vì đúng domain
- Đây là **confirmation bias có hệ thống**: user chọn domain → hệ thống ưu tiên domain đó → user thấy "đúng" → nhưng kết quả tốt nhất có thể nằm ở domain khác
- **Không có A/B test** để chứng minh boosting cải thiện precision

**Vấn đề với taxonomy cố định:**

- Tài liệu xuyên domain: thông tư về "môi trường trong xây dựng" thuộc domain nào?
- Chi phí bảo trì: cần chuyên gia phân loại, cập nhật liên tục
- Cấu trúc 2 cấp cố định (L1 > L2) không mở rộng được
- Nghiên cứu: metadata filtering (hard filter) hiệu quả hơn soft boosting (xem mục 4.3)

**Cách langflow-ai/openrag làm - faceted search:**

Họ dùng facets phẳng, độc lập (`data_sources`, `document_types`, `owners`, `connectors`):
- Một tài liệu đồng thời là `type=pdf` + `source=sharepoint` + `owner=legal_team` mà không cần "chọn domain"
- Facets **tự động** từ metadata có sẵn, không cần người phân loại
- Aggregations trả kèm mỗi search: "có 45 PDF từ legal team, 12 từ HR" → user/agent tự quyết định lọc gì
- Linh hoạt hơn, không tạo bias, không cần bảo trì

### 4.3 Xác minh các tuyên bố - Bằng chứng thực vs. Giả định

> Cập nhật: 2026-04-01. Fact-check kỹ các claims đã trích dẫn ở trên.

#### TaxoIndex (EMNLP 2024) - Paper thật, nhưng context rất hẹp

Paper tồn tại thật ([ACL Anthology](https://aclanthology.org/2024.emnlp-main.407/)):
- **+30% NDCG** chỉ đo trên **CSFCube** (academic paper search), queries có high lexical mismatch
- Con số peak thực tế: **+56.35% N@5** trên subset khó nhất
- Dataset dùng taxonomy **431K nodes** từ Microsoft Academic - curated bởi Microsoft
- Kỹ thuật: train thêm neural module (6.7% params) để nhận diện concepts từ taxonomy
- **KHÔNG liên quan đến legal search** và **KHÔNG phải** chỉ tạo domain labels rồi boost score
- **Kết luận: Không áp dụng được cho cách open-rag dùng domain taxonomy**

#### "Hard filter > soft boost" - Ý kiến phổ biến, KHÔNG phải nghiên cứu

Không tìm được paper nào so sánh trực tiếp. Đây là **folk wisdom** từ search engineering:

| Tình huống | Hard filter tốt hơn | Soft boost tốt hơn |
|---|---|---|
| Metadata chính xác (số hiệu VB, năm) | **Đúng** | |
| Metadata có thể sai/thiếu | | **Đúng** |
| User biết chính xác cần gì | **Đúng** | |
| Exploratory search (khám phá) | | **Đúng** |
| Dataset nhỏ (filter loại quá nhiều) | | **Đúng** |

Cho legal metadata chính xác → hard filter hợp lý. Cho domain/subject mơ hồ → soft boost vẫn có giá trị.

#### EMNLP 2024 "Searching for Best Practices in RAG" - KHÔNG nói về filtering

Paper thật (Wang et al., 2024), nhưng tập trung vào chunking, retrieval methods, reranking.
**Không đề cập metadata filtering hay faceted search.** Trích dẫn cho filtering strategies là sai context.

#### Self-Query Retrieval - Ý tưởng hay, thực tế đầy lỗi

**Concept:** LLM tự parse natural language → structured metadata filters.

**Ai đang làm:**
- **LangChain SelfQueryRetriever** - phổ biến nhất
- **NVIDIA RAG Blueprint** - native "natural language filter generation"
- **Haystack** - extract metadata from queries
- **Multi-Meta-RAG** (paper 2024) - **+25.6% accuracy** trên MultiHop-RAG benchmark

**Production issues nghiêm trọng** (GitHub langchain-ai/langchain):

| Issue | Mô tả |
|---|---|
| [#9761](https://github.com/langchain-ai/langchain/issues/9761) | LLM tạo **filter attributes không tồn tại** → crash app |
| [#13593](https://github.com/langchain-ai/langchain/issues/13593) | Sinh sai kiểu dữ liệu (date thay vì integer) |
| [#15919](https://github.com/langchain-ai/langchain/issues/15919) | Toán tử AND **fail** khi query nhiều metadata fields |
| [#29711](https://github.com/langchain-ai/langchain/issues/29711) | SelfQueryRetriever malfunction tổng quát |

**Vấn đề cốt lõi:** LLM sinh filter trông hợp lý nhưng sai ngầm → crash hoặc kết quả sai.

**Giải pháp tốt hơn cho legal domain: Rule-based extraction trước, LLM sau**

```
Regex/pattern matching (0ms latency, 100% predictable):
- "Nghị định|NĐ"     → document_type = "nghi_dinh"
- "Thông tư|TT"      → document_type = "thong_tu"
- "Luật|BLLĐ"        → document_type = "luat"
- "số 145/2020"      → document_number CONTAINS "145/2020"
- "năm 2024"         → date filter 2024
- "BLLĐ"             → expand "Bộ luật Lao động"

Chỉ fallback sang LLM khi rule-based không parse được.
```

Đây cũng là approach Haystack recommend: structured extraction bằng rules trước, LLM sau.

#### Tổng kết xác minh

| Tuyên bố | Mức độ bằng chứng |
|---|---|
| TaxoIndex +30% NDCG | **Thật nhưng không áp dụng được** (academic search, neural module) |
| Hard filter > soft boost | **Ý kiến, không phải nghiên cứu.** Phụ thuộc ngữ cảnh |
| EMNLP 2024 về filtering | **Trích dẫn sai.** Paper không nói về filtering strategies |
| Self-Query Retrieval trend | **Có thật** nhưng LLM-based có nhiều lỗi production. Rule-based tốt hơn cho legal |
| Multi-Meta-RAG +25.6% | **Thật**, trên MultiHop-RAG benchmark cụ thể |

---

## 5. Hướng đi mới: Học theo langflow-ai/openrag

> Nguyên tắc: **Đứng trên vai người khổng lồ.** Copy theo cách làm đã được chứng minh
> trong production, thay vì tự nghĩ ra cách riêng chưa được kiểm chứng.

### 5.1 Kiến trúc Search của langflow-ai/openrag (tham khảo)

```
Hybrid Search 3 tầng:
├── Semantic KNN (70% weight)
│   ├── HNSW index (ef_construction=100, m=16)
│   ├── k=50, num_candidates=1000
│   └── Multi-model embedding support
├── BM25 Keyword (30% weight)
│   ├── Multi-match: text^2, filename^1.5
│   ├── Fuzziness: AUTO:4,7
│   └── OR operator
└── Prefix Fallback (25% weight)
    └── match_phrase_prefix cho partial input

Post-retrieval:
├── Score threshold filtering
├── Aggregations (5 dimensions) trả kèm results
└── Document ACL filtering
```

### 5.2 Search Parameters của langflow-ai/openrag

```python
POST /api/search
{
  "query": str,
  "filters": {
    "data_sources": ["file1.pdf", "file2.docx"],   # filter by filename
    "document_types": ["application/pdf"],           # filter by mimetype
    "owners": ["user@email.com"],                    # filter by owner
    "connector_types": ["google_drive"]              # filter by source
  },
  "limit": 10,                    # top_k
  "scoreThreshold": 0.5           # minimum score
}

Response:
{
  "results": [{ filename, page, text, score, embedding_model }],
  "aggregations": {
    "data_sources": [{ key, doc_count }],
    "document_types": [{ key, doc_count }],
    "owners": [{ key, doc_count }],
    "connector_types": [{ key, doc_count }],
    "embedding_models": [{ key, doc_count }]
  }
}
```

### 5.3 Document Management của langflow-ai/openrag

**Document Schema:**
- `filename`, `mimetype`, `page`, `text`
- `source_url`, `owner`, `owner_name`, `owner_email`
- `connector_type`, `embedding_model`, `embedding_dimensions`
- `allowed_users`, `allowed_groups` (ACL)
- `file_size`, `file_hash` (dedup)

**Features:**
- Hash-based document deduplication
- Multi-embedding support (query embed với tất cả models có sẵn)
- Connectors: S3, Google Drive, OneDrive, IBM COS
- Knowledge Filters: save/reuse search filter presets
- Chunking: 1000 chars default, 200 overlap
- Docling integration: OCR, table extraction, image descriptions

### 5.4 Những gì open-rag nên copy

**Ưu tiên cao (copy trực tiếp):**

1. **Score threshold parameter** - Thêm `scoreThreshold` vào SearchRequest
2. **Search aggregations/facets** - Trả kèm thống kê theo metadata dimensions
3. **Content hash dedup** - Hash file khi upload để phát hiện trùng lặp
4. **Prefix fallback** trong hybrid search - Xử lý partial input tốt hơn

**Ưu tiên trung bình (adapt cho legal domain):**

5. **Knowledge Filters** - Save/reuse filter presets (rất hữu ích cho legal search)
6. **Document ACL** - `allowed_users`, `allowed_groups` per document
7. **Rule-based query normalization** - Mở rộng viết tắt pháp luật, chuẩn hóa số hiệu
   - "BLLĐ" → "Bộ luật Lao động"
   - "NĐ 145/2020" → "Nghị định 145/2020/NĐ-CP"

**Xem xét lại (giảm bớt hoặc tắt):**

8. **Multi-Query & HyDE** - Chuyển thành optional, tắt mặc định. Chỉ bật khi có evaluation pipeline
9. **Domain soft boost** - Giảm weight hoặc chuyển sang hard filter. Cần A/B test trước khi giữ
10. **Domain taxonomy** - Giữ như metadata bổ sung, không phải trung tâm search strategy

### 5.5 Những gì open-rag KHÔNG nên copy (giữ lại lợi thế riêng)

- **Legal metadata extraction** - Đây là lợi thế domain-specific thực sự
- **Configurable chunking per collection** - langflow-ai/openrag chỉ có global config
- **Custom heading detection scripts** - Hữu ích cho tài liệu pháp luật có cấu trúc đặc thù
- **Hybrid search** - Đã có, tiếp tục duy trì

---

## 6. Bảng so sánh Vector Databases

| Tính năng | Pinecone | Qdrant | Weaviate | Milvus | Vectara |
|---|---|---|---|---|---|
| **Loại** | Managed | OSS (Rust) | OSS (Go) | OSS (Go/C++) | Managed |
| **Hybrid Search** | Sparse-dense | Native | Native (BM25F) | Milvus 2.4+ | Native |
| **Filtering** | Metadata | **Advanced** (pre-filter) | Metadata + inverted index | Attribute filter | Metadata |
| **Multi-tenancy** | Namespace | **First-class** | **First-class** | Partition/DB | Corpus |
| **Reranking** | Không native | Không native | Không native | Không native | Native (MMR) |
| **Scale** | Tốt (managed) | Tốt | Tốt | **Tốt nhất** (tỷ vectors) | Tốt |

---

*Sources:*
- *Firecrawl, PremAI, Weaviate, LiquidMetal AI, TensorBlue, Superlinked, Orkes, Data Nucleus, Vectara Docs*
- *RAGO (ISCA 2025), ZenML, ACM Medical QA 2025*
- *[TaxoIndex - ACL Anthology (EMNLP 2024)](https://aclanthology.org/2024.emnlp-main.407/)*
- *[Multi-Meta-RAG (2024)](https://arxiv.org/abs/2406.13213)*
- *[NVIDIA RAG Blueprint - Advanced Metadata Filtering](https://docs.nvidia.com/rag/2.3.0/custom-metadata.html)*
- *[Haystack - Extract Metadata from Queries](https://haystack.deepset.ai/blog/extracting-metadata-filter)*
- *[LangChain SelfQueryRetriever issues: #9761, #13593, #15919, #29711](https://github.com/langchain-ai/langchain/issues/9761)*
- *[langflow-ai/openrag](https://github.com/langflow-ai/openrag)*
