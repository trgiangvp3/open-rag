# OpenRAG — Feature Roadmap & Analysis

## Tóm tắt độ ưu tiên

| Chức năng | Độ khó | Impact |
|-----------|--------|--------|
| Reranker | Thấp | Cao — cải thiện ngay chất lượng search |
| RAG generate + citation | Trung bình | Cao — UX thay đổi hoàn toàn |
| Hybrid search | Trung bình | Trung bình — tốt với tài liệu kỹ thuật |
| Progress WebSocket | Thấp | Trung bình — UX tốt hơn |
| Multi-turn Q&A | Cao | Cao — nhưng cần LLM layer trước |
| OCR | Thấp | Tùy — chỉ cần nếu có PDF scan |

---

## 1. Tăng chất lượng tìm kiếm

### 1.1 Hybrid Search (Semantic + BM25)

**Vấn đề:** Semantic search thiếu nhạy với exact match — mã sản phẩm, tên riêng, số hiệu dễ bị bỏ sót.

**Giải pháp:** Chạy song song semantic search và BM25 keyword search, merge bằng Reciprocal Rank Fusion:
```
score_final = 1/(k + rank_semantic) + 1/(k + rank_bm25)
```
Chunk xuất hiện cao ở cả hai bảng xếp hạng sẽ được ưu tiên.

**Phù hợp khi:** tài liệu có nhiều mã số, thuật ngữ kỹ thuật, tên riêng.

---

### 1.2 Reranker (Cross-Encoder)

**Vấn đề:** Vector search (bi-encoder) encode query và document độc lập → thiếu hiểu ngữ cảnh.

**Giải pháp:** Workflow 2 tầng:
```
Vector search → top 50 chunks (nhanh)
    ↓
Cross-encoder reranker → re-score 50 chunks → trả về top 5 (chính xác)
```

**Model gợi ý:** `BAAI/bge-reranker-v2-m3` (multilingual, hỗ trợ tiếng Việt).

**Ưu điểm:** Tăng precision rõ rệt, không cần thay đổi cách lưu trữ, chỉ thêm bước post-processing.

---

### 1.3 Query Expansion / HyDE

**Vấn đề:** Câu hỏi ngắn, mơ hồ → embedding không đủ thông tin.

**Giải pháp A — Multi-query:** LLM sinh nhiều cách diễn đạt khác nhau, embed tất cả, merge kết quả:
```
"lương thử việc"
  → "mức lương trong thời gian thử việc là bao nhiêu?"
  → "quy định về lương probation"
  → "chính sách đãi ngộ nhân viên mới"
```

**Giải pháp B — HyDE:** LLM sinh đoạn văn giả định chứa câu trả lời, embed đoạn văn đó thay vì câu hỏi.

**Chi phí:** Thêm 1 LLM call mỗi query.

---

## 2. Tăng khả năng xử lý tài liệu

### 2.1 OCR cho PDF scan / ảnh

**Vấn đề:** Markitdown core không xử lý được PDF dạng ảnh scan (không có text layer).

**Giải pháp:** `markitdown-ocr` — tự detect trang không có text → gửi ảnh trang lên LLM vision → nhận lại text.

**Chi phí:** Tốn token LLM cho mỗi trang scan. Cần cân nhắc nếu tài liệu nhiều trang.

---

### 2.2 Batch Upload

**Hiện tại:** Upload từng file, mỗi file là 1 request.

**Cần thêm:**
- Endpoint nhận nhiều file cùng lúc
- Xử lý song song (asyncio + thread pool)
- Response trả về summary: số file thành công / thất bại / tổng chunks

---

### 2.3 Auto Re-index

**Mục đích:** Watch thư mục, khi file thay đổi → tự xóa chunks cũ → re-index.

**Library:** `watchdog`

**Hữu ích khi:** tài liệu được cập nhật thường xuyên (quy trình, chính sách nội bộ...).

---

## 3. Tầng LLM / RAG đầy đủ

### 3.1 Generate câu trả lời

**Hiện tại:** Hệ thống chỉ retrieve — trả về danh sách chunks, user tự đọc và tổng hợp.

**RAG đầy đủ:** retrieve → đưa chunks vào prompt → LLM tổng hợp câu trả lời:
```
System: Dựa vào các đoạn tài liệu sau, trả lời câu hỏi...
Context: [chunk 1] [chunk 2] [chunk 3]
User: lương thử việc là bao nhiêu?
Assistant: Theo quy định tại mục 3.2, lương thử việc bằng 85%...
```

---

### 3.2 Trích dẫn nguồn

LLM trả lời kèm `[1]`, `[2]`... và list nguồn ở cuối, giúp user verify.

**Quan trọng** với tài liệu nội bộ — không thể tin mù câu trả lời AI.

---

### 3.3 Multi-turn Q&A

Giữ lịch sử hội thoại để câu hỏi sau có thể tham chiếu câu trước:
```
User: chính sách nghỉ phép là gì?
AI: [trả lời]
User: thế nghỉ ốm thì sao?  ← "thế" cần biết context trước
```

**Cần thêm:** session management + history summarization.

**Phụ thuộc:** cần hoàn thiện 3.1 trước.

---

## 4. Vận hành & quan sát

### 4.1 Progress Tracking (WebSocket)

**Vấn đề:** Indexing file lớn mất thời gian, UI không biết đang ở bước nào.

**Giải pháp:** WebSocket push real-time:
```
[1/4] Converting PDF...
[2/4] Chunking (42 chunks)...
[3/4] Embedding...
[4/4] Storing to ChromaDB... Done!
```

---

### 4.2 API Key Authentication

Bảo vệ API khỏi truy cập trái phép. Header `Authorization: Bearer <key>` trên mọi request.

**Implementation:** FastAPI `Security` dependency.

---

### 4.3 Logging & Audit Trail

Ghi lại mỗi query: thời gian, nội dung câu hỏi, chunks trả về, score.

**Dùng để:**
- Debug khi kết quả sai
- Phân tích câu hỏi phổ biến để cải thiện tài liệu
- Compliance (một số tổ chức yêu cầu audit log)
