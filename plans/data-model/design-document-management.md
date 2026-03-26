# Thiết kế: Quản lý Document nâng cao — Versioning, Lifecycle, Metadata mở rộng

> **Trạng thái**: Draft
> **Ngày tạo**: 2026-03-26
> **Phạm vi**: Database schema, API, ML service, Frontend

---

## 1. Bối cảnh & Vấn đề

### 1.1 Hiện trạng

Hệ thống hiện tại có 2 entity đơn giản:

```
Collection (Id, Name, Description, CreatedAt)
    └── Document (Id, Filename, CollectionId, ChunkCount, SizeBytes, CreatedAt, IndexedAt, Status)
```

- Collection chỉ là namespace/folder
- Document chỉ là file được upload, không có metadata nghiệp vụ
- Không quản lý phiên bản
- Không theo dõi hiệu lực
- Không phân loại loại văn bản
- Chunk metadata trong ChromaDB chỉ có: `{filename, section, document_id, indexed_at}`

### 1.2 Yêu cầu thực tế

Hệ thống phục vụ **đa dạng loại tài liệu** doanh nghiệp:

| Loại | Ví dụ | Metadata đặc thù |
|------|-------|-------------------|
| Sổ tay chất lượng (QMS) | QMS-MN-001ver1.8 | Người phê duyệt, bộ phận ban hành |
| Quy định nội bộ (QĐNB) | 29-10.QDNB 1.0 | Cơ quan ban hành, phạm vi áp dụng |
| Quy trình (PROC) | QT-IT-003 | Chủ quy trình, input/output |
| Biểu mẫu (FORM) | BM-HR-012 | Mã biểu mẫu, bộ phận sử dụng |
| Hướng dẫn công việc (WI) | HD-QA-007 | Vị trí áp dụng |
| Hợp đồng | HD-2024-0156 | Bên ký kết, giá trị, thời hạn |
| Tài liệu kỹ thuật | SPEC-PRD-042 | Sản phẩm, tiêu chuẩn tham chiếu |
| Tài liệu pháp lý | NĐ-168/2024 | Cơ quan ban hành, ngày có hiệu lực |
| Tài liệu đào tạo | Training materials | Đối tượng, thời lượng |

Các yêu cầu chung:
- **Versioning**: cùng 1 văn bản có nhiều phiên bản (1.0 → 1.5 → 1.8)
- **Thời hạn hiệu lực**: ngày có hiệu lực, ngày hết hạn/bị thay thế
- **Metadata linh hoạt**: mỗi loại văn bản cần tập metadata riêng
- **Tìm kiếm thông minh**: filter theo loại, phiên bản, trạng thái, thời gian

---

## 2. Thiết kế tổng quan

### 2.1 Ý tưởng cốt lõi: Tách LogicalDocument vs DocumentRevision

```
Collection (1) ──── (*) LogicalDocument (1) ──── (*) DocumentRevision
                              │                          │
                         has Tags (M:N)           stored in ChromaDB
                         has MetadataValues       has chunk embeddings
                              │
                    DocumentType (1) ──── (*) MetadataFieldDefinition
```

- **LogicalDocument**: khái niệm văn bản xuyên suốt các phiên bản
  - VD: "Sổ tay chất lượng QMS-MN-001" — tồn tại dù có 10 phiên bản
- **DocumentRevision**: file cụ thể được upload — thay thế entity `Document` hiện tại
  - VD: "QMS-MN-001ver1.8.docx uploaded 2017-09-15"
- **DocumentType**: phân loại văn bản — định nghĩa tập metadata riêng per loại
- **MetadataFieldDefinition + DocumentMetadataValue**: EAV pattern cho metadata mở rộng

### 2.2 Tại sao thiết kế này?

| Quyết định | Lý do | Thay thế đã cân nhắc |
|-----------|-------|----------------------|
| Tách LogicalDocument / Revision | User cần quản lý phiên bản → cần persistent identity | Thêm version fields vào Document — không thể hiện mối quan hệ supersession |
| Collection giữ đơn giản | Complexity nằm ở document management, không phải collection structure | Collection thành hierarchy — over-engineering |
| EAV cho custom metadata | User nói "các loại thông tin khác có thể có nhiều khía cạnh khác" → schema phải mở rộng không cần migration | JSON column — khó validate, khó render dynamic form |
| Tags riêng biệt với Type | Type = phân loại chính thức (1:N), Tags = phân loại phụ (M:N) | Chỉ dùng Type — không đủ linh hoạt cho cross-cutting concerns |

---

## 3. Database Schema chi tiết

### 3.1 Entity: `DocumentType`

Định nghĩa loại văn bản. Seed sẵn các loại phổ biến, admin có thể thêm mới.

```csharp
public class DocumentType
{
    public int Id { get; set; }
    public string Code { get; set; } = "";        // "QMS", "QDNB", "PROC", "CONTRACT"
    public string Name { get; set; } = "";         // "Sổ tay chất lượng"
    public string? Description { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    public ICollection<MetadataFieldDefinition> FieldDefinitions { get; set; } = [];
    public ICollection<LogicalDocument> LogicalDocuments { get; set; } = [];
}
```

**Index**: `Code` (unique)

**Seed data**:

| Id | Code | Name |
|----|------|------|
| 1 | QMS | Sổ tay chất lượng |
| 2 | PROC | Quy trình |
| 3 | QDNB | Quy định nội bộ |
| 4 | FORM | Biểu mẫu |
| 5 | WI | Hướng dẫn công việc |
| 6 | CONTRACT | Hợp đồng |
| 7 | TECH | Tài liệu kỹ thuật |
| 8 | LEGAL | Tài liệu pháp lý |
| 9 | TRAINING | Tài liệu đào tạo |
| 10 | OTHER | Khác |

### 3.2 Entity: `MetadataFieldDefinition`

Định nghĩa 1 field metadata tùy chỉnh cho 1 loại văn bản.

```csharp
public class MetadataFieldDefinition
{
    public int Id { get; set; }
    public int DocumentTypeId { get; set; }
    public DocumentType DocumentType { get; set; } = null!;

    public string FieldKey { get; set; } = "";          // "approver", "department"
    public string DisplayName { get; set; } = "";        // "Người phê duyệt", "Bộ phận"
    public string FieldType { get; set; } = "text";      // "text" | "date" | "number" | "select"
    public string? SelectOptions { get; set; }            // JSON array cho "select": ["HR","IT","QA"]
    public bool IsRequired { get; set; } = false;
    public int SortOrder { get; set; } = 0;
}
```

**Index**: `(DocumentTypeId, FieldKey)` unique

**Seed data** (ví dụ cho một số loại):

| DocumentType | FieldKey | DisplayName | FieldType |
|-------------|----------|-------------|-----------|
| QMS | approver | Người phê duyệt | text |
| QMS | department | Bộ phận ban hành | text |
| PROC | process_owner | Chủ quy trình | text |
| QDNB | scope | Phạm vi áp dụng | text |
| QDNB | issuing_authority | Cơ quan ban hành | text |
| FORM | usage_department | Bộ phận sử dụng | text |
| CONTRACT | party | Bên ký kết | text |
| CONTRACT | value | Giá trị hợp đồng | number |
| CONTRACT | duration | Thời hạn | text |
| LEGAL | issuing_body | Cơ quan ban hành | text |
| TRAINING | target_audience | Đối tượng | text |
| TRAINING | duration_hours | Thời lượng (giờ) | number |

### 3.3 Entity: `LogicalDocument`

Văn bản logic — tồn tại xuyên suốt các phiên bản.

```csharp
public class LogicalDocument
{
    public int Id { get; set; }
    public string DocumentCode { get; set; } = "";    // "QMS-MN-001", "29-10.QDNB", "" (nếu không có)
    public string Title { get; set; } = "";            // "Sổ tay chất lượng"
    public int CollectionId { get; set; }
    public Collection Collection { get; set; } = null!;
    public int? DocumentTypeId { get; set; }           // nullable — không bắt buộc phân loại
    public DocumentType? DocumentType { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    public ICollection<DocumentRevision> Revisions { get; set; } = [];
    public ICollection<DocumentTag> Tags { get; set; } = [];
    public ICollection<DocumentMetadataValue> MetadataValues { get; set; } = [];
}
```

**Indexes**:
- `(CollectionId, DocumentCode)` unique — cùng collection không trùng mã
- `DocumentTypeId`

### 3.4 Entity: `DocumentRevision`

Thay thế entity `Document` hiện tại. Mỗi revision là 1 file cụ thể đã upload + embed.

```csharp
public class DocumentRevision
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public int LogicalDocumentId { get; set; }
    public LogicalDocument LogicalDocument { get; set; } = null!;

    // ── Từ Document cũ ────────────────────────────
    public string Filename { get; set; } = "";
    public long SizeBytes { get; set; }
    public int ChunkCount { get; set; }
    public string Status { get; set; } = "indexing";     // indexing | indexed | failed
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? IndexedAt { get; set; }

    // ── Versioning (MỚI) ──────────────────────────
    public string VersionLabel { get; set; } = "1.0";    // free-text: "1.0", "ver1.8", "Rev.A"
    public int VersionNumber { get; set; } = 1;           // tăng dần, dùng để sắp xếp
    public bool IsCurrent { get; set; } = true;            // chỉ 1 revision/LogicalDocument là current

    // ── Lifecycle (MỚI) ───────────────────────────
    public string LifecycleStatus { get; set; } = "draft";
        // draft | effective | superseded | expired | withdrawn
    public DateTime? EffectiveDate { get; set; }           // ngày có hiệu lực
    public DateTime? ExpiryDate { get; set; }              // ngày hết hạn
    public Guid? SupersededById { get; set; }              // revision nào thay thế revision này
    public string? ChangeNotes { get; set; }               // ghi chú thay đổi
}
```

**Indexes**:
- `LogicalDocumentId`
- `Status`
- `LifecycleStatus`
- `IsCurrent`
- `(LogicalDocumentId, VersionNumber)` unique

### 3.5 Entity: `Tag` + `DocumentTag`

Gắn thẻ cross-cutting, không phụ thuộc loại văn bản hay collection.

```csharp
public class Tag
{
    public int Id { get; set; }
    public string Name { get; set; } = "";         // "ISO 9001", "Bộ phận HR", "Quan trọng"
    public string? Color { get; set; }              // hex color cho UI, VD: "#e74c3c"
    public ICollection<DocumentTag> DocumentTags { get; set; } = [];
}

public class DocumentTag
{
    public int LogicalDocumentId { get; set; }
    public LogicalDocument LogicalDocument { get; set; } = null!;
    public int TagId { get; set; }
    public Tag Tag { get; set; } = null!;
}
```

**Index**: `Tag.Name` unique, `DocumentTag` composite PK `(LogicalDocumentId, TagId)`

### 3.6 Entity: `DocumentMetadataValue`

EAV — lưu giá trị metadata tùy chỉnh.

```csharp
public class DocumentMetadataValue
{
    public int Id { get; set; }
    public int LogicalDocumentId { get; set; }
    public LogicalDocument LogicalDocument { get; set; } = null!;
    public int FieldDefinitionId { get; set; }
    public MetadataFieldDefinition FieldDefinition { get; set; } = null!;
    public string Value { get; set; } = "";       // lưu dạng string, parse theo FieldType
}
```

**Index**: `(LogicalDocumentId, FieldDefinitionId)` unique

### 3.7 Entity: `Collection` — thay đổi tối thiểu

```csharp
public class Collection
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public string? Icon { get; set; }              // MỚI: icon identifier cho UI
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public ICollection<LogicalDocument> LogicalDocuments { get; set; } = [];
}
```

### 3.8 ER Diagram

```
┌──────────────┐     ┌───────────────────┐     ┌─────────────────────┐
│  Collection   │ 1:N │  LogicalDocument   │ 1:N │  DocumentRevision    │
├──────────────┤     ├───────────────────┤     ├─────────────────────┤
│ Id           │────▶│ Id                │────▶│ Id (Guid)           │
│ Name (uniq)  │     │ DocumentCode      │     │ LogicalDocumentId   │
│ Description  │     │ Title             │     │ Filename            │
│ Icon?        │     │ CollectionId (FK) │     │ SizeBytes           │
│ CreatedAt    │     │ DocumentTypeId?   │     │ ChunkCount          │
└──────────────┘     │ CreatedAt         │     │ Status              │
                     │ UpdatedAt         │     │ CreatedAt           │
                     └───────┬───────────┘     │ IndexedAt?          │
                             │                 │ VersionLabel        │
                     ┌───────┼──────┐          │ VersionNumber       │
                     │       │      │          │ IsCurrent           │
                  M:N│    1:N│   N:1│          │ LifecycleStatus     │
                     ▼       ▼      ▼          │ EffectiveDate?      │
               ┌─────────┐ ┌──────────────┐   │ ExpiryDate?         │
               │   Tag    │ │MetadataValue │   │ SupersededById?     │
               ├─────────┤ ├──────────────┤   │ ChangeNotes?        │
               │ Id      │ │ Id           │   └─────────────────────┘
               │ Name    │ │ LogDocId(FK) │
               │ Color?  │ │ FieldDefId   │   ┌─────────────────────┐
               └─────────┘ │ Value        │   │   DocumentType       │
                           └──────────────┘   ├─────────────────────┤
                                              │ Id                  │
                                              │ Code (uniq)         │
                                              │ Name                │
                                              │ Description?        │
                                              └──────────┬──────────┘
                                                         │ 1:N
                                                         ▼
                                              ┌─────────────────────┐
                                              │MetadataFieldDef      │
                                              ├─────────────────────┤
                                              │ Id                  │
                                              │ DocumentTypeId (FK) │
                                              │ FieldKey            │
                                              │ DisplayName         │
                                              │ FieldType           │
                                              │ SelectOptions?      │
                                              │ IsRequired          │
                                              │ SortOrder           │
                                              └─────────────────────┘
```

---

## 4. Versioning — Luồng xử lý chi tiết

### 4.1 Upload file: xác định new doc hay new revision

```
User upload file + (optional) document_code, version_label, effective_date, document_type_code
    │
    ├── document_code provided?
    │     ├── YES → Tìm LogicalDocument có code này trong collection
    │     │           ├── Found → Tạo DocumentRevision mới (new version)
    │     │           │           → Chạy supersession flow (mục 4.2)
    │     │           └── Not found → Tạo LogicalDocument mới + Revision đầu tiên
    │     │
    │     └── NO → Tạo LogicalDocument mới (code = "", title = filename)
    │              + Revision đầu tiên
    │
    └── Sau đó: chunk + embed + store vào ChromaDB với enriched metadata
```

### 4.2 Supersession flow — khi upload phiên bản mới

```
1. DocumentRevision mới:
   - IsCurrent = true
   - LifecycleStatus = "effective" (hoặc "draft" nếu chưa có EffectiveDate)
   - VersionNumber = max(existing) + 1

2. DocumentRevision cũ (bản current trước đó):
   - IsCurrent = false
   - LifecycleStatus = "superseded"
   - SupersededById = new_revision.Id
   - ExpiryDate = new_revision.EffectiveDate ?? DateTime.UtcNow

3. ChromaDB metadata update (cho chunks của revision cũ):
   - is_current: "true" → "false"
   - lifecycle_status: "effective" → "superseded"
   - Dùng collection.update() — KHÔNG cần re-embed
```

### 4.3 Lifecycle states

```
                    ┌──────────────────────────────────┐
                    │                                  ▼
 [Upload] ──► draft ──► effective ──► superseded (bị thay thế)
                              │
                              ├──► expired (quá hạn)
                              │
                              └──► withdrawn (thu hồi thủ công)
```

| State | Ý nghĩa | Trigger |
|-------|---------|---------|
| `draft` | Vừa upload, chưa có hiệu lực | Upload không có EffectiveDate |
| `effective` | Đang có hiệu lực | EffectiveDate đã đến |
| `superseded` | Bị thay thế bởi phiên bản mới | Upload revision mới |
| `expired` | Quá hạn hiệu lực | ExpiryDate đã qua |
| `withdrawn` | Thu hồi thủ công | Admin action |

### 4.4 Search behavior theo lifecycle

| Tình huống | Mặc định | Tùy chọn |
|-----------|---------|---------|
| Tìm kiếm thông thường | Chỉ chunks từ revision `IsCurrent=true` | Toggle "tìm tất cả phiên bản" |
| Kết quả từ bản superseded | Không hiển thị | Hiển thị + warning badge |
| Kết quả từ bản expired | Không hiển thị | Hiển thị + warning badge |
| RAG generation context | Chỉ từ bản current | Có thể include tất cả + ghi chú version |

### 4.5 Supersession chain — hiển thị UI

```
📄 QMS-MN-001 — Sổ tay chất lượng
├── v1.0 (2015-01-01 → 2016-06-15)  ⚪ superseded
├── v1.5 (2016-06-15 → 2017-09-15)  ⚪ superseded
└── v1.8 (2017-09-15 → hiện tại)    🟢 effective  ← current
```

---

## 5. ChromaDB Integration

### 5.1 Enriched chunk metadata

Hiện tại mỗi chunk trong ChromaDB chỉ có:
```python
{"filename": "...", "section": "...", "document_id": "...", "indexed_at": "..."}
```

Sau redesign, mỗi chunk sẽ có:
```python
{
    # Giữ nguyên
    "document_id": "guid-of-revision",          # Guid của DocumentRevision
    "filename": "QMS-MN-001ver1.8.docx",
    "section": "3.2 Chính sách chất lượng",
    "indexed_at": "2026-03-26T10:00:00Z",

    # Mới — cho filtering
    "logical_document_id": "42",                 # int → string (ChromaDB constraint)
    "document_code": "QMS-MN-001",
    "version_label": "1.8",
    "version_number": "3",                       # int → string
    "is_current": "true",                        # bool → string
    "lifecycle_status": "effective",
    "effective_date": "2017-09-15",              # ISO date string
    "document_type_code": "QMS",
    "collection": "documents",
}
```

> **Lưu ý**: ChromaDB metadata chỉ hỗ trợ string, int, float, bool — không hỗ trợ nested objects hay arrays. Thiết kế trên tuân thủ constraint này.

### 5.2 Search filtering qua ChromaDB `where`

```python
# Mặc định: chỉ tìm bản hiện hành
where = {"is_current": {"$eq": "true"}}

# Filter theo loại văn bản
where = {"$and": [
    {"is_current": {"$eq": "true"}},
    {"document_type_code": {"$eq": "QMS"}},
]}

# Filter theo khoảng ngày hiệu lực
where = {"$and": [
    {"is_current": {"$eq": "true"}},
    {"effective_date": {"$gte": "2020-01-01"}},
    {"effective_date": {"$lte": "2025-12-31"}},
]}

# Tìm tất cả phiên bản của 1 văn bản
where = {"document_code": {"$eq": "QMS-MN-001"}}
```

### 5.3 Update metadata khi supersede

Khi revision cũ bị supersede, cần update metadata trên tất cả chunks:

```python
# ML service endpoint mới: POST /ml/documents/update-metadata
def update_chunk_metadata(collection_name, document_id, metadata_updates):
    collection = client.get_collection(collection_name)
    results = collection.get(where={"document_id": document_id}, include=[])
    if results["ids"]:
        new_metadatas = [{**existing, **metadata_updates} for existing in ...]
        collection.update(ids=results["ids"], metadatas=new_metadatas)
```

Ưu điểm: ChromaDB `collection.update()` chỉ thay đổi metadata, **không cần re-embed**.

### 5.4 Backfill metadata cho chunks cũ

Chunks đã tồn tại trong ChromaDB cần được bổ sung metadata mới. Thực hiện qua 1 management endpoint hoặc script:

1. Lấy tất cả DocumentRevision từ database
2. Với mỗi revision, update ChromaDB chunks:
   - Thêm `logical_document_id`, `document_code`, `version_label`, `version_number`
   - Set `is_current`, `lifecycle_status`, `document_type_code`
3. Dùng `collection.update()` — batch per revision

---

## 6. API Design

### 6.1 Document Types — CRUD

```
GET    /api/document-types
       Response: [{ id, code, name, description, fieldDefinitions: [...] }]

POST   /api/document-types
       Body: { code, name, description? }

PUT    /api/document-types/{id}
       Body: { name, description? }

DELETE /api/document-types/{id}
       → Set LogicalDocument.DocumentTypeId = null cho docs thuộc type này

POST   /api/document-types/{id}/fields
       Body: { fieldKey, displayName, fieldType, selectOptions?, isRequired, sortOrder }

PUT    /api/document-types/{id}/fields/{fieldId}
       Body: { displayName, fieldType, selectOptions?, isRequired, sortOrder }

DELETE /api/document-types/{id}/fields/{fieldId}
       → Xóa DocumentMetadataValue tương ứng
```

### 6.2 Tags — CRUD

```
GET    /api/tags
       Response: [{ id, name, color, documentCount }]

POST   /api/tags
       Body: { name, color? }

DELETE /api/tags/{id}
```

### 6.3 Logical Documents

```
GET    /api/collections/{collectionName}/documents
       Query: ?typeCode=QMS&tag=ISO9001&search=keyword
       Response: [{
           id, documentCode, title,
           documentTypeName?, tags: ["ISO 9001", "HR"],
           currentRevision: { id, versionLabel, lifecycleStatus, effectiveDate, chunkCount },
           revisionCount, createdAt
       }]

POST   /api/collections/{collectionName}/documents
       Body: { documentCode, title, documentTypeCode?, tags?: [] }

GET    /api/collections/{collectionName}/documents/{id}
       Response: {
           id, documentCode, title, documentType, tags,
           customMetadata: { "approver": "Nguyễn Văn A", "department": "QA" },
           revisions: [{ id, versionLabel, lifecycleStatus, ... }]
       }

PUT    /api/collections/{collectionName}/documents/{id}
       Body: { documentCode?, title?, documentTypeCode?, tags?: [], customMetadata?: {} }

DELETE /api/collections/{collectionName}/documents/{id}
       → Xóa LogicalDocument + tất cả Revisions + chunks trong ChromaDB
```

### 6.4 Revisions

```
GET    /api/documents/{logicalDocId}/revisions
       Response: [{ id, versionLabel, versionNumber, isCurrent, lifecycleStatus, ... }]

POST   /api/documents/{logicalDocId}/revisions/upload
       Form: file, versionLabel?, effectiveDate?, changeNotes?
       → Tạo revision mới + supersede revision cũ

PUT    /api/documents/{logicalDocId}/revisions/{revisionId}
       Body: { lifecycleStatus?, effectiveDate?, expiryDate?, changeNotes? }

DELETE /api/documents/{logicalDocId}/revisions/{revisionId}
       → Xóa revision + chunks; nếu là current → bản mới nhất còn lại thành current

POST   /api/documents/{logicalDocId}/revisions/{revisionId}/set-current
       → Đặt revision này làm current (ít dùng — cho trường hợp rollback)
```

### 6.5 Upload — backward compatible + enhanced

```
POST   /api/documents/upload
       Form: file, collection,
             document_code?,          ← MỚI (optional)
             version_label?,          ← MỚI
             effective_date?,         ← MỚI
             document_type_code?      ← MỚI

Logic:
- Nếu KHÔNG có document_code → tạo LogicalDocument mới (title=filename) + Revision
- Nếu CÓ document_code và trùng → tạo Revision mới dưới LogicalDocument đã tồn tại
- Nếu CÓ document_code và không trùng → tạo LogicalDocument mới + Revision
```

### 6.6 Search — enhanced filters

```
POST   /api/search
       Body: {
           query, collection, topK, useReranker, searchMode, generate,
           // MỚI:
           currentOnly: true,           // mặc định chỉ search bản hiện hành
           documentTypeCode?: "QMS",    // filter theo loại
           documentCode?: "QMS-MN-001", // filter theo mã văn bản cụ thể
           lifecycleStatus?: "effective",// "effective", "all", ...
           effectiveDateFrom?: "2020-01-01",
           effectiveDateTo?: "2025-12-31",
           tags?: ["ISO 9001"]
       }
```

.NET backend build ChromaDB `where` clause từ các filter params, gửi cho ML service.

### 6.7 ML Service — endpoint mới

```python
# POST /ml/documents/update-metadata
class UpdateMetadataRequest(BaseModel):
    document_id: str                    # Guid của revision
    collection: str
    metadata_updates: dict              # key-value pairs to update

# POST /ml/search — thêm where_filter
class SearchRequest(BaseModel):
    query: str
    collection: str
    top_k: int = 5
    use_reranker: bool = False
    search_mode: str = "semantic"
    where_filter: dict | None = None    # MỚI — ChromaDB where clause
```

---

## 7. Frontend Design

### 7.1 DocumentsTab — redesign

**Hiện tại**: flat list filenames
**Sau**: grouped by document type, hiển thị LogicalDocument

```
┌─────────────────────────────────────────────────────────────┐
│ 📋 Collection: documents                                   │
│                                                             │
│ ┌─ Filter ─────────────────────────────────────────────────┐│
│ │ Loại: [Tất cả ▾]  Tag: [...]  Trạng thái: [Hiện hành ▾]││
│ └──────────────────────────────────────────────────────────┘│
│                                                             │
│ Mã          Tên                  Loại    Version  Trạng thái│
│ ─────────── ──────────────────── ─────── ──────── ─────────│
│ QMS-MN-001  Sổ tay chất lượng   QMS     v1.8     🟢 effective│
│   ├── v1.0 (2015-01-01)  ⚪ superseded                     │
│   ├── v1.5 (2016-06-15)  ⚪ superseded                     │
│   └── v1.8 (2017-09-15)  🟢 effective ← current           │
│                                                             │
│ 29-10.QDNB  Quản lý mã khóa... QDNB    v1.0     🟢 effective│
│                                                             │
│ (no code)   Training_slides.pptx OTHER   v1.0     🟡 draft  │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 UploadTab — enhanced

```
┌─────────────────────────────────────────────────────────────┐
│ 📤 Upload tài liệu                                        │
│                                                             │
│ Collection: [documents ▾]                                   │
│                                                             │
│ ┌── Thông tin văn bản (tùy chọn) ──────────────────────────┐│
│ │ Mã văn bản: [QMS-MN-001    ]  ← auto-detect từ filename ││
│ │ Phiên bản:  [1.8            ]                            ││
│ │ Loại:       [Sổ tay chất lượng ▾]                        ││
│ │ Ngày hiệu lực: [2017-09-15  📅]                         ││
│ │                                                          ││
│ │ ⚠ Văn bản QMS-MN-001 đã tồn tại — sẽ tạo phiên bản mới ││
│ └──────────────────────────────────────────────────────────┘│
│                                                             │
│ ┌──── Kéo thả file vào đây ────────────────────────────────┐│
│ │                                                          ││
│ │              📁  Chọn file                               ││
│ │                                                          ││
│ └──────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### 7.3 SearchTab — filter panel

```
┌─────────────────────────────────────────────────────────────┐
│ 🔍 [tìm kiếm văn bản...                        ] [Tìm]   │
│                                                             │
│ ┌── Bộ lọc ────────────────────────────────────────────────┐│
│ │ Loại: [Tất cả ▾]  Trạng thái: [Hiện hành ▾]            ││
│ │ Từ ngày: [____] → Đến ngày: [____]                      ││
│ │ Tags: [ISO 9001 ×] [HR ×] [+]                           ││
│ │ ☑ Chỉ bản hiện hành  ☑ Dùng Reranker                   ││
│ └──────────────────────────────────────────────────────────┘│
│                                                             │
│ Kết quả:                                                    │
│ ┌──────────────────────────────────────────────────────────┐│
│ │ 📄 QMS-MN-001 v1.8 (QMS) 🟢                      81.3% ││
│ │ 3.2 Chính sách chất lượng                                ││
│ │ Công ty cam kết duy trì hệ thống quản lý chất lượng...  ││
│ └──────────────────────────────────────────────────────────┘│
│ ┌──────────────────────────────────────────────────────────┐│
│ │ ⚠ QMS-MN-001 v1.0 (QMS) ⚪ superseded             42.1% ││
│ │ 2.1 Phạm vi                                             ││
│ │ [Phiên bản này đã bị thay thế bởi v1.8]                 ││
│ └──────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### 7.4 Components mới

| Component | Chức năng |
|-----------|-----------|
| `DocumentTypeManager.vue` | Admin: CRUD loại văn bản + field definitions |
| `TagManager.vue` | Admin: CRUD tags |
| `DocumentDetailPanel.vue` | Side panel: chi tiết doc + revision history + custom metadata |
| `MetadataForm.vue` | Dynamic form render từ field definitions, dùng chung cho Upload + Detail |

### 7.5 Auto-detect document code/version từ filename

```
Filename: "QMS-MN-001ver1.8(Sotaychatluong)_20170915.docx"
→ code: "QMS-MN-001", version: "1.8", date: 2017-09-15

Filename: "29-10.QDNB 1.0 (Quy dinh ve quan ly ma khoa tu ket sat).docx"
→ code: "29-10.QDNB", version: "1.0"

Regex patterns:
  ^(?<code>[A-Z0-9._-]+?)ver(?<version>[\d.]+)        # CODE + "ver" + VERSION
  ^(?<code>[A-Z0-9._-]+)\s+(?<version>[\d.]+)         # CODE + space + VERSION
  _(?<date>\d{8})\.                                     # Date suffix _YYYYMMDD
```

Best-effort — user luôn override được trong form upload.

---

## 8. Migration Strategy

### Phase 1: Database schema + backward compat

1. **EF Core Migration "AddDocumentManagementSchema"**:
   - Tạo tables mới: DocumentTypes, MetadataFieldDefinitions, LogicalDocuments, DocumentRevisions, Tags, DocumentTags, DocumentMetadataValues
   - Seed document types + field definitions
   - CHƯA xóa table Documents cũ

2. **EF Core Migration "MigrateDocumentsToRevisions"** (data migration):
   ```sql
   -- Mỗi Document cũ → 1 LogicalDocument + 1 DocumentRevision
   INSERT INTO LogicalDocuments (DocumentCode, Title, CollectionId, CreatedAt, UpdatedAt)
   SELECT '', Filename, CollectionId, CreatedAt, CreatedAt FROM Documents;

   INSERT INTO DocumentRevisions (Id, LogicalDocumentId, Filename, SizeBytes, ...)
   SELECT d.Id, ld.Id, d.Filename, d.SizeBytes, ...
   FROM Documents d JOIN LogicalDocuments ld ON ...;
   ```

3. **API backward compat**: Map responses từ schema mới cho API cũ

### Phase 2: Backend services + ML integration

1. Implement services mới: DocumentTypeService, TagService, LogicalDocumentService
2. Refactor DocumentService: versioning flow + enriched metadata
3. ML service: thêm `/ml/documents/update-metadata`, search `where_filter`
4. ChromaDB metadata backfill cho chunks cũ

### Phase 3: Frontend

1. Build admin screens (DocumentTypeManager, TagManager)
2. Redesign DocumentsTab
3. Enhance UploadTab + SearchTab với metadata fields + filters

### Phase 4: Polish

1. Auto-detect code/version từ filename (FilenameParser)
2. Lifecycle badges + warnings trong search results
3. Revision history timeline UI
4. RAG prompt enhancement: include document metadata trong context

---

## 9. Tác động lên Search Quality

Enriched metadata cải thiện RAG:

**Trước**:
```
[1] Công ty cam kết duy trì hệ thống quản lý chất lượng...
```

**Sau**:
```
[1] (QMS-MN-001 v1.8 — Sổ tay chất lượng, hiệu lực 2017-09-15, 🟢 effective)
    3.2 Chính sách chất lượng
    Công ty cam kết duy trì hệ thống quản lý chất lượng...
```

LLM có thêm thông tin để:
- Trích dẫn chính xác nguồn (mã + phiên bản)
- Cảnh báo khi thông tin từ bản cũ/hết hạn
- Ưu tiên thông tin từ bản hiện hành

---

## 10. Files tạo/sửa (tham khảo khi implement)

### Tạo mới

**Backend entities** (~7 files):
- `OpenRAG.Api/Models/Entities/DocumentType.cs`
- `OpenRAG.Api/Models/Entities/MetadataFieldDefinition.cs`
- `OpenRAG.Api/Models/Entities/LogicalDocument.cs`
- `OpenRAG.Api/Models/Entities/DocumentRevision.cs`
- `OpenRAG.Api/Models/Entities/Tag.cs`
- `OpenRAG.Api/Models/Entities/DocumentTag.cs`
- `OpenRAG.Api/Models/Entities/DocumentMetadataValue.cs`

**Backend services + controllers** (~6 files):
- `OpenRAG.Api/Services/DocumentTypeService.cs`
- `OpenRAG.Api/Services/TagService.cs`
- `OpenRAG.Api/Services/LogicalDocumentService.cs`
- `OpenRAG.Api/Services/FilenameParser.cs`
- `OpenRAG.Api/Controllers/DocumentTypesController.cs`
- `OpenRAG.Api/Controllers/TagsController.cs`

**Backend DTOs** (~5 files):
- Request/Response models cho LogicalDocument, Revision, DocumentType

**Frontend** (~6 files):
- `frontend/src/components/DocumentTypeManager.vue`
- `frontend/src/components/TagManager.vue`
- `frontend/src/components/DocumentDetailPanel.vue`
- `frontend/src/components/MetadataForm.vue`
- `frontend/src/stores/documentTypes.ts`
- `frontend/src/stores/tags.ts`

### Sửa đổi

- `OpenRAG.Api/Data/AppDbContext.cs` — DbSets, config, seed
- `OpenRAG.Api/Services/DocumentService.cs` — versioning flow
- `OpenRAG.Api/Services/MlClient.cs` — update metadata, where filter
- `OpenRAG.Api/Controllers/DocumentsController.cs` — enhanced upload
- `OpenRAG.Api/Controllers/SearchController.cs` — pass filters
- `OpenRAG.Api/Models/Entities/Collection.cs` — thêm Icon
- `OpenRAG.Api/Program.cs` — register services
- `ml_service/main_ml.py` — update-metadata endpoint, where filter
- `ml_service/schemas_ml.py` — new request models
- `ml_service/rag/store.py` — update_metadata(), where param
- `frontend/src/api/index.ts` — types + API functions
- `frontend/src/components/DocumentsTab.vue` — redesign
- `frontend/src/components/UploadTab.vue` — metadata fields
- `frontend/src/components/SearchTab.vue` — filter panel
