# Status: Legal Document Classification & Parsing

**Plan**: [legal-document-classification.md](legal-document-classification.md)
**Created**: 2026-03-30
**Status**: Implementation complete, ready for testing

---

## Phase Tracking

| Phase | Mô tả | Status | Files |
|-------|--------|--------|-------|
| 1 | DB Schema — Document Metadata | [x] Done | Document.cs, migration |
| 2 | Legal HTML Parser (TVPL) | [x] Done | LegalHtmlParser.cs, LegalDocumentMetadata.cs |
| 3 | Legal Document Chunker | [x] Done | IChunker.cs, LegalDocumentChunker.cs, MarkdownChunker.cs |
| 4 | Integration vào Pipeline | [x] Done | DocumentService.cs, DocumentsController.cs |
| 5 | Metadata-Filtered Search | [x] Done | SearchRequest.cs, SearchController.cs, MlClient.cs, store.py, main_ml.py, hybrid_search.py, schemas_ml.py |
| 6 | API & Frontend | [x] Done | DocumentsController.cs, api/index.ts, SearchTab.vue, DocumentsTab.vue |
| 7 | Backfill & Admin | [x] Done | DocumentsController.cs (reparse endpoint) |

## New Files Created

- `OpenRAG.Api/Services/Parsing/LegalDocumentMetadata.cs`
- `OpenRAG.Api/Services/Parsing/LegalHtmlParser.cs`
- `OpenRAG.Api/Services/Chunking/IChunker.cs`
- `OpenRAG.Api/Services/Chunking/LegalDocumentChunker.cs`

## Dependencies Added

- `HtmlAgilityPack` 1.12.4

## Decisions Log

| Ngày | Quyết định | Lý do |
|------|-----------|-------|
| 2026-03-30 | Luồng riêng cho HTML TVPL, không qua markdown | HTML có anchor names chuẩn hóa — parse chính xác 100% |
| 2026-03-30 | Xử lý hoàn toàn trong C# | Phần cứng hạn chế, ML service chỉ dùng khi bắt buộc |
| 2026-03-30 | Tags dùng comma-separated string | Đơn giản, không cần relational query phức tạp |
| 2026-03-30 | BM25 dùng post-filter thay vì Tantivy schema migration | Đơn giản hơn, tránh rebuild index |
