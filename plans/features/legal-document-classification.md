# Plan: Legal Document Classification & Faceted Search

## Context

Hệ thống RAG cho văn bản quy phạm pháp luật (VBQPPL) từ nguồn HTML Thư viện Pháp luật (TVPL).

**Kiến trúc:**
- Luồng riêng cho HTML pháp luật — parse HTML trực tiếp (không qua markdown)
- Xử lý hoàn toàn trong C# — ML service chỉ embed + store + search
- 2 facet chính: **Lĩnh vực** (Domain, taxonomy 2 cấp) + **Đối tượng áp dụng** (Subjects)
- Search UX: **Soft boost** (không hard filter, over-fetch + re-score)

## Quyết định thiết kế chunking

- **Document prefix** (tên VB, số hiệu): **BỎ khỏi chunk text** — giống nhau ở mọi chunk, đã có trong metadata
- **Section path** (Chương > Điều > Khoản): **GIỮ trong chunk text** — chứa keyword chủ đề thiết yếu (VD: "Sử dụng tài khoản vốn"), bỏ đi chunk mất context

## Implementation Status

### Phase 1: DB Schema [Done]
- Document entity: 12 metadata fields (DocumentType, Number, Title, Authority, Date, LegalBasis, Terminology, ReferencedDocs)
- Domain entity: taxonomy 2 cấp (Domains table, seed data)
- Document.DomainId (FK), SuggestedDomainsJson, SubjectsJson
- Migration: AddDocumentLegalMetadata + AddDomainsAndFacets

### Phase 2: Legal HTML Parser [Done]
- `LegalHtmlParser.cs` — parse anchors TVPL (loai_1, dieu_X, chuong_X)
- `ExtractSubjects()` — parse Điều 2 "Đối tượng áp dụng"
- `SuggestDomains()` — authority (0.9) → title keywords (0.8) → TVPL URLs (0.5)
- Auto-assign domain nếu confidence >= 0.7

### Phase 3: Legal Document Chunker [Done]
- `LegalDocumentChunker.cs` — chunk theo Điều/Khoản/Điểm
- Section path prefix (giữ) + document prefix (bỏ)
- Thuật ngữ = 1 chunk riêng, căn cứ pháp lý = 1 chunk riêng
- Chunk metadata: domain, domain_parent, subjects, document_type, document_number, issue_date

### Phase 4: Pipeline Integration [Done]
- HTML detect → LegalHtmlParser → LegalDocumentChunker → ML embed
- Non-HTML → ML convert markdown → MarkdownChunker (unchanged)

### Phase 5: Search [Done]
- Hard filter (ChromaDB WHERE): DocumentType, DateFrom, DateTo
- Soft boost (.NET side): DomainSlug (+0.15 L2 / +0.08 L1), Subject (+0.07)
- Over-fetch topK*3 → boost → take topK

### Phase 6: API [Done]
- `DomainsController` — CRUD domains (tree)
- `PUT /documents/{id}/domain` — accept/change domain
- `GET /documents/{id}/metadata` — full metadata
- `GET /api/tags`, `PUT /documents/{id}/tags`

### Phase 7: Frontend [Done]
- SearchTab: Lĩnh vực dropdown (tree), Đối tượng áp dụng input, Loại VB, Date range
- DocumentsTab: Metadata panel, domain display
- api/index.ts: DomainInfo type, new endpoints

## Files Created
- `OpenRAG.Api/Models/Entities/Domain.cs`
- `OpenRAG.Api/Controllers/DomainsController.cs`
- `OpenRAG.Api/Services/Parsing/LegalHtmlParser.cs`
- `OpenRAG.Api/Services/Parsing/LegalDocumentMetadata.cs`
- `OpenRAG.Api/Services/Chunking/IChunker.cs`
- `OpenRAG.Api/Services/Chunking/LegalDocumentChunker.cs`

## Dependencies
- `HtmlAgilityPack` 1.12.4
