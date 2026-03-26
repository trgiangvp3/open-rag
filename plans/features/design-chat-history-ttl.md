# Thiết kế: Giới hạn Lịch sử Chat & TTL Phiên làm việc

**File**: `plans/design-chat-history-ttl.md`
**Status**: Design proposal — not yet implemented
**Date**: 2026-03-26

---

## 1. Phát biểu vấn đề

`ChatService.ChatAsync()` tải toàn bộ lịch sử tin nhắn của một phiên thông qua eager-load của EF Core (`.Include(s => s.Messages)`). Không có cơ chế dọn dẹp, không có giới hạn, và không có phân trang. Điều này dẫn đến bốn chế độ lỗi tích lũy:

| Lỗi | Nguyên nhân gốc rễ | Triệu chứng quan sát được |
|---|---|---|
| DB tăng trưởng không giới hạn | Không có TTL cho phiên hoặc tin nhắn | Kích thước file SQLite tăng mãi mãi |
| Tải phiên chậm | Quét toàn bộ bảng cho mọi tin nhắn của một phiên | Độ trễ yêu cầu tăng theo tuổi phiên |
| LLM cắt bớt âm thầm | Toàn bộ lịch sử được truyền thô đến LLM API | Các lượt cũ bị model cắt ngầm do giới hạn context window; citations bị gãy |
| Người dùng không nhận biết được | Số lượng token/tin nhắn không được hiển thị lên frontend | Người dùng không thể biết khi lịch sử của họ bị bỏ qua |

Cột `UpdatedAt` trên `ChatSession` là tín hiệu hoạt động duy nhất, nhưng nó không bao giờ được truy vấn để dọn dẹp và không có index. Hiện tại có ba migration (`InitialCreate`, `AddChatSessions`, `AddPerformanceIndexes`) tạo ra schema hiện tại.

---

## 2. Tổng quan giải pháp

Sáu thay đổi phối hợp:

1. **Session TTL** — `BackgroundService` định kỳ xóa các phiên không hoạt động quá N ngày.
2. **Message sliding window** — `ChatService` chỉ gửi N tin nhắn cuối cùng đến LLM, không bao giờ gửi toàn bộ lịch sử.
3. **Paginated history API** — `GET /api/chat/{sessionId}/history?page=1&pageSize=50` với tổng số và metadata phân trang.
4. **DB migration** — Cột `LastActivityAt` + index trên `ChatSessions`; composite index trên `ChatMessages(SessionId, CreatedAt)`.
5. **Configuration** — Tất cả các ngưỡng trong `appsettings.json` dưới `Chat:*`, được bind qua `IOptions<ChatOptions>`.
6. **Frontend** — Phát hiện phiên hết hạn, chỉ báo sử dụng context theo từng lượt, phân trang load-more.

---

## 3. Chi tiết từng giải pháp con

### 3.1 Cấu hình (`appsettings.json` + Options class)

#### `OpenRAG.Api/appsettings.json` — thêm section `Chat`

```json
{
  "ConnectionStrings": {
    "Default": "Data Source=../data/openrag.db"
  },
  "MlService": {
    "BaseUrl": "http://localhost:8001"
  },
  "Llm": {
    "BaseUrl": "",
    "ApiKey": "",
    "Model": "gpt-4o-mini"
  },
  "Chat": {
    "SessionTtlDays": 30,
    "MaxMessagesInContext": 20,
    "CleanupIntervalHours": 6,
    "HistoryPageSize": 50
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning"
    }
  },
  "AllowedHosts": "*",
  "Urls": "http://0.0.0.0:8000"
}
```

**Ý nghĩa các tham số**:

- `SessionTtlDays` — các phiên không có hoạt động trong số ngày này sẽ bị xóa. `0` vô hiệu hóa hoàn toàn việc dọn dẹp.
- `MaxMessagesInContext` — số lượng tin nhắn tối đa được gửi đến LLM mỗi yêu cầu. Áp dụng dưới dạng sliding window lấy đuôi lịch sử.
- `CleanupIntervalHours` — tần suất chạy job dọn dẹp nền.
- `HistoryPageSize` — kích thước trang mặc định cho endpoint lịch sử có phân trang.

#### File mới: `OpenRAG.Api/Options/ChatOptions.cs`

```csharp
namespace OpenRAG.Api.Options;

/// <summary>
/// Strongly-typed binding for the "Chat" section in appsettings.json.
/// Register with: builder.Services.Configure<ChatOptions>(builder.Configuration.GetSection("Chat"));
/// </summary>
public class ChatOptions
{
    public const string SectionName = "Chat";

    /// <summary>Sessions idle longer than this are deleted by the cleanup job. 0 = disabled.</summary>
    public int SessionTtlDays { get; set; } = 30;

    /// <summary>Maximum number of history messages forwarded to the LLM per request.</summary>
    public int MaxMessagesInContext { get; set; } = 20;

    /// <summary>How often the cleanup background service runs.</summary>
    public int CleanupIntervalHours { get; set; } = 6;

    /// <summary>Default page size for GET /api/chat/{id}/history.</summary>
    public int HistoryPageSize { get; set; } = 50;
}
```

---

### 3.2 DB Migration — `LastActivityAt` + composite index

#### Lý do dùng `LastActivityAt` thay vì tái sử dụng `UpdatedAt`

`UpdatedAt` đúng về mặt ngữ nghĩa nhưng không có index và giá trị của nó được gán qua `session.UpdatedAt = DateTime.UtcNow` rải rác trong code thay vì tập trung. Đổi tên nó sẽ là một thay đổi phá vỡ tên cột trong SQLite (yêu cầu tạo lại bảng). Thay vào đó, thêm một cột có index chuyên dụng `LastActivityAt` mà job nền truy vấn độc quyền, và giữ `UpdatedAt` cho mục đích audit.

#### Migration mới: `OpenRAG.Api/Data/Migrations/YYYYMMDDHHMMSS_AddSessionTtlSupport.cs`

(Chạy `dotnet ef migrations add AddSessionTtlSupport` sau khi áp dụng các thay đổi model bên dưới, rồi thay thế phần body tự sinh bằng:)

```csharp
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace OpenRAG.Api.Data.Migrations
{
    public partial class AddSessionTtlSupport : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // 1. Add LastActivityAt — backfill from UpdatedAt so no session is
            //    incorrectly expired on first deploy.
            migrationBuilder.AddColumn<DateTime>(
                name: "LastActivityAt",
                table: "ChatSessions",
                type: "TEXT",
                nullable: false,
                defaultValueSql: "UpdatedAt");   // SQLite supports column reference in DEFAULT

            // 2. Index for the cleanup job's WHERE clause:
            //    SELECT * FROM ChatSessions WHERE LastActivityAt < @cutoff
            migrationBuilder.CreateIndex(
                name: "IX_ChatSessions_LastActivityAt",
                table: "ChatSessions",
                column: "LastActivityAt");

            // 3. Composite index for the sliding-window query:
            //    SELECT TOP N FROM ChatMessages WHERE SessionId = @id ORDER BY CreatedAt DESC
            //    The existing IX_ChatMessages_SessionId covers the WHERE; adding CreatedAt
            //    makes ORDER BY a pure index scan (no sort step).
            migrationBuilder.DropIndex(
                name: "IX_ChatMessages_SessionId",
                table: "ChatMessages");

            migrationBuilder.CreateIndex(
                name: "IX_ChatMessages_SessionId_CreatedAt",
                table: "ChatMessages",
                columns: new[] { "SessionId", "CreatedAt" });
        }

        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_ChatMessages_SessionId_CreatedAt",
                table: "ChatMessages");

            migrationBuilder.CreateIndex(
                name: "IX_ChatMessages_SessionId",
                table: "ChatMessages",
                column: "SessionId");

            migrationBuilder.DropIndex(
                name: "IX_ChatSessions_LastActivityAt",
                table: "ChatSessions");

            migrationBuilder.DropColumn(
                name: "LastActivityAt",
                table: "ChatSessions");
        }
    }
}
```

> **Lưu ý zero-downtime**: `ALTER TABLE ADD COLUMN` của SQLite với `DEFAULT` không gây block. Binary cũ vẫn có thể chạy trên schema mới — nó chỉ đơn giản bỏ qua `LastActivityAt`. Binary mới sẽ ghi vào nó. Có một khoảng thời gian ngắn mà các phiên từ binary cũ có `LastActivityAt = UpdatedAt` (được backfill bởi giá trị mặc định), điều này là đúng đắn. Không có table lock, không mất dữ liệu.

#### Thay đổi trong `AppDbContext.OnModelCreating`

File: `OpenRAG.Api/Data/AppDbContext.cs`

Thay thế phần cấu hình `ChatMessageEntity` và thêm block cấu hình `ChatSession`:

```csharp
protected override void OnModelCreating(ModelBuilder modelBuilder)
{
    modelBuilder.Entity<Collection>(e =>
    {
        e.HasIndex(c => c.Name).IsUnique();
    });

    modelBuilder.Entity<Document>(e =>
    {
        e.HasOne(d => d.Collection)
         .WithMany(c => c.Documents)
         .HasForeignKey(d => d.CollectionId)
         .OnDelete(DeleteBehavior.Cascade);

        e.HasIndex(d => d.Status);
        e.HasIndex(d => d.CollectionId);
    });

    modelBuilder.Entity<ChatSession>(e =>
    {
        // Index used by the cleanup background job
        e.HasIndex(s => s.LastActivityAt);
    });

    modelBuilder.Entity<ChatMessageEntity>(e =>
    {
        e.HasOne(m => m.Session)
         .WithMany(s => s.Messages)
         .HasForeignKey(m => m.SessionId)
         .OnDelete(DeleteBehavior.Cascade);

        // Composite index replaces the simple SessionId index.
        // Supports: WHERE SessionId = ? ORDER BY CreatedAt DESC LIMIT N
        e.HasIndex(m => new { m.SessionId, m.CreatedAt });
    });

    // Seed default collection
    modelBuilder.Entity<Collection>().HasData(new Collection
    {
        Id = 1,
        Name = "documents",
        Description = "Default collection",
        CreatedAt = new DateTime(2024, 1, 1, 0, 0, 0, DateTimeKind.Utc),
    });
}
```

#### Cập nhật entity `ChatSession`

File: `OpenRAG.Api/Models/Entities/ChatSession.cs`

```csharp
namespace OpenRAG.Api.Models.Entities;

public class ChatSession
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Collection { get; set; } = "documents";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    /// <summary>
    /// Updated on every user interaction. Used exclusively by the cleanup job
    /// for TTL comparisons. Has a dedicated DB index.
    /// </summary>
    public DateTime LastActivityAt { get; set; } = DateTime.UtcNow;

    public ICollection<ChatMessageEntity> Messages { get; set; } = [];
}
```

---

### 3.3 Session TTL — Background Cleanup Service

#### File mới: `OpenRAG.Api/Services/ChatCleanupService.cs`

```csharp
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using OpenRAG.Api.Data;
using OpenRAG.Api.Options;

namespace OpenRAG.Api.Services;

/// <summary>
/// Background service that periodically deletes ChatSessions whose
/// LastActivityAt is older than Chat:SessionTtlDays.
///
/// Cascade delete on the FK means ChatMessages are removed automatically.
/// The service uses a dedicated DI scope per run to avoid holding a
/// long-lived DbContext across sleep intervals.
/// </summary>
public sealed class ChatCleanupService(
    IServiceScopeFactory scopeFactory,
    IOptions<ChatOptions> options,
    ILogger<ChatCleanupService> logger)
    : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var opts = options.Value;

        if (opts.SessionTtlDays <= 0)
        {
            logger.LogInformation(
                "ChatCleanupService: SessionTtlDays = {Days} — cleanup disabled.",
                opts.SessionTtlDays);
            return;
        }

        logger.LogInformation(
            "ChatCleanupService started. TTL = {Days} days, interval = {Hours} h.",
            opts.SessionTtlDays, opts.CleanupIntervalHours);

        // Run immediately on startup, then on the configured interval.
        while (!stoppingToken.IsCancellationRequested)
        {
            await RunCleanupAsync(opts, stoppingToken);

            try
            {
                await Task.Delay(
                    TimeSpan.FromHours(opts.CleanupIntervalHours),
                    stoppingToken);
            }
            catch (OperationCanceledException)
            {
                // Application is shutting down — exit cleanly.
                break;
            }
        }
    }

    private async Task RunCleanupAsync(ChatOptions opts, CancellationToken ct)
    {
        try
        {
            await using var scope = scopeFactory.CreateAsyncScope();
            var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();

            var cutoff = DateTime.UtcNow.AddDays(-opts.SessionTtlDays);

            // EF Core translates this to a single DELETE … WHERE statement.
            // Cascade on the FK deletes ChatMessages automatically.
            var deleted = await db.ChatSessions
                .Where(s => s.LastActivityAt < cutoff)
                .ExecuteDeleteAsync(ct);

            if (deleted > 0)
                logger.LogInformation(
                    "ChatCleanupService: deleted {Count} expired session(s) (cutoff = {Cutoff:O}).",
                    deleted, cutoff);
            else
                logger.LogDebug(
                    "ChatCleanupService: no expired sessions found (cutoff = {Cutoff:O}).",
                    cutoff);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // Log and continue — a transient DB error must not crash the host.
            logger.LogError(ex, "ChatCleanupService: cleanup run failed.");
        }
    }
}
```

**Các quyết định thiết kế quan trọng**:

- `IServiceScopeFactory` + `CreateAsyncScope()` — `AppDbContext` được đăng ký là `Scoped`. `BackgroundService` là `Singleton`, vì vậy nó không thể inject `AppDbContext` trực tiếp. Tạo một scope mới cho mỗi lần chạy tránh anti-pattern của DbContext tồn tại lâu dài.
- `ExecuteDeleteAsync` — bulk-delete của EF Core 7+. Dịch thành `DELETE FROM ChatSessions WHERE LastActivityAt < ?`. Không có đối tượng nào được tạo trong bộ nhớ; cascade ở cấp DB xóa các tin nhắn liên quan.
- Guard với `SessionTtlDays <= 0` — operator có thể vô hiệu hóa việc dọn dẹp mà không cần xóa đăng ký service.
- `catch` nuốt các lỗi tạm thời thay vì làm crash tiến trình host.

#### Đăng ký trong `Program.cs`

Thêm sau các đăng ký service hiện có:

```csharp
// ── Chat configuration ────────────────────────────────────────────────────
builder.Services.Configure<ChatOptions>(
    builder.Configuration.GetSection(ChatOptions.SectionName));

// ── Background services ───────────────────────────────────────────────────
builder.Services.AddHostedService<ChatCleanupService>();
```

---

### 3.4 Message Sliding Window trong `ChatService`

Thay đổi nằm ở bước xây dựng lịch sử (bước 3) của `ChatAsync`. Thay vì tải tất cả tin nhắn qua `.Include()`, truy vấn trực tiếp chỉ N tin nhắn cuối từ DB được sắp xếp theo `CreatedAt DESC LIMIT N`, rồi đảo ngược để lấy thứ tự thời gian. Điều này tránh việc tạo hàng nghìn tin nhắn trong bộ nhớ chỉ để lấy phần đuôi.

Ngoài ra, `LastActivityAt` phải được cập nhật trong mỗi lần tương tác.

File: `OpenRAG.Api/Services/ChatService.cs` — file cập nhật đầy đủ:

```csharp
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Dto.Shared;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Options;

namespace OpenRAG.Api.Services;

public class ChatService(
    AppDbContext db,
    MlClient ml,
    LlmClient llm,
    IOptions<ChatOptions> options,
    ILogger<ChatService> logger)
{
    private ChatOptions Opts => options.Value;

    public async Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
    {
        // 1. Get or create session — do NOT load Messages here.
        ChatSession session;
        if (req.SessionId.HasValue)
        {
            // Load session header only; messages are fetched separately below.
            session = await db.ChatSessions
                .FirstOrDefaultAsync(s => s.Id == req.SessionId.Value, ct)
                ?? throw new KeyNotFoundException($"Session {req.SessionId} not found");
        }
        else
        {
            session = new ChatSession { Collection = req.Collection };
            db.ChatSessions.Add(session);
            await db.SaveChangesAsync(ct);
        }

        // 2. Retrieve relevant chunks from the ML service.
        var chunks = await ml.SearchAsync(
            req.Query, req.Collection, req.TopK,
            req.UseReranker, req.SearchMode, ct);

        // 3. Build a SLIDING WINDOW of the last N messages for LLM context.
        //    Query is descending-by-date limited to MaxMessagesInContext rows,
        //    then reversed in memory to restore chronological order.
        //    The composite index (SessionId, CreatedAt) makes this an index-range
        //    scan with no sort step.
        var windowSize = Opts.MaxMessagesInContext;
        var history = await db.ChatMessages
            .Where(m => m.SessionId == session.Id)
            .OrderByDescending(m => m.CreatedAt)
            .Take(windowSize)
            .Select(m => new ChatMessage(m.Role, m.Content))
            .ToListAsync(ct);

        history.Reverse(); // Restore chronological order for the LLM.

        var totalMessageCount = await db.ChatMessages
            .CountAsync(m => m.SessionId == session.Id, ct);

        // 4. Generate answer (optional — only when LLM is configured).
        GenerateResult? generated = null;
        if (llm.IsEnabled)
            generated = await llm.GenerateAsync(req.Query, chunks, history, ct);

        // 5. Persist this turn.
        db.ChatMessages.Add(new ChatMessageEntity
        {
            SessionId = session.Id,
            Role = "user",
            Content = req.Query,
        });

        if (generated is not null)
            db.ChatMessages.Add(new ChatMessageEntity
            {
                SessionId = session.Id,
                Role = "assistant",
                Content = generated.Answer,
            });

        // 6. Update activity timestamps.
        var now = DateTime.UtcNow;
        session.UpdatedAt = now;
        session.LastActivityAt = now;

        await db.SaveChangesAsync(ct);

        logger.LogInformation(
            "Chat session {SessionId}: query processed, {ChunkCount} chunks retrieved, " +
            "{WindowSize}/{TotalMessages} messages in context",
            session.Id, chunks.Count, Math.Min(windowSize, totalMessageCount), totalMessageCount);

        return new ChatResponse(
            session.Id,
            generated?.Answer,
            generated?.Citations,
            chunks,
            ContextStats: new ContextUsageStats(
                MessagesInContext: Math.Min(windowSize, totalMessageCount),
                TotalMessages: totalMessageCount,
                MaxMessagesInContext: windowSize,
                IsTruncated: totalMessageCount > windowSize));
    }

    public async Task<ChatHistoryResponse?> GetHistoryAsync(
        Guid sessionId,
        int page,
        int pageSize,
        CancellationToken ct = default)
    {
        var session = await db.ChatSessions
            .FirstOrDefaultAsync(s => s.Id == sessionId, ct);

        if (session is null) return null;

        var totalMessages = await db.ChatMessages
            .CountAsync(m => m.SessionId == sessionId, ct);

        var messages = await db.ChatMessages
            .Where(m => m.SessionId == sessionId)
            .OrderBy(m => m.CreatedAt)
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .Select(m => new ChatMessage(m.Role, m.Content))
            .ToListAsync(ct);

        var totalPages = (int)Math.Ceiling(totalMessages / (double)pageSize);

        return new ChatHistoryResponse(
            SessionId: session.Id,
            Messages: messages,
            Page: page,
            PageSize: pageSize,
            TotalMessages: totalMessages,
            TotalPages: totalPages,
            HasNextPage: page < totalPages,
            HasPreviousPage: page > 1);
    }

    public async Task DeleteSessionAsync(Guid sessionId, CancellationToken ct = default)
    {
        var session = await db.ChatSessions.FindAsync([sessionId], ct);
        if (session is not null)
        {
            db.ChatSessions.Remove(session);
            await db.SaveChangesAsync(ct);
        }
    }
}
```

**Tại sao dùng hai query thay vì Include + LINQ Take?**

`.Include(s => s.Messages).Take(N)` không hoạt động như mong đợi: EF Core áp dụng `Take` cho query sessions, không phải cho collection messages được include. Bạn sẽ tải tất cả tin nhắn và áp dụng `Take` trong bộ nhớ. Cách viết tường minh `.Where(m => m.SessionId == ...).OrderByDescending(...).Take(N)` tạo ra SQL chính xác.

---

### 3.5 Response DTOs

File: `OpenRAG.Api/Models/Dto/Responses/ChatResponse.cs` — file cập nhật đầy đủ:

```csharp
using OpenRAG.Api.Models.Dto.Shared;

namespace OpenRAG.Api.Models.Dto.Responses;

/// <summary>
/// Returned by POST /api/chat.
/// ContextStats is null when the LLM is disabled (no answer generated).
/// </summary>
public record ChatResponse(
    Guid SessionId,
    string? Answer,
    List<int>? Citations,
    List<ChunkResult> Chunks,
    ContextUsageStats? ContextStats = null
);

/// <summary>How much of the session history was forwarded to the LLM.</summary>
public record ContextUsageStats(
    int MessagesInContext,
    int TotalMessages,
    int MaxMessagesInContext,
    bool IsTruncated
);

/// <summary>
/// Returned by GET /api/chat/{sessionId}/history.
/// Supports pagination — always request page 1 for the oldest messages.
/// </summary>
public record ChatHistoryResponse(
    Guid SessionId,
    List<ChatMessage> Messages,
    int Page,
    int PageSize,
    int TotalMessages,
    int TotalPages,
    bool HasNextPage,
    bool HasPreviousPage
);
```

---

### 3.6 Cập nhật Controller

File: `OpenRAG.Api/Controllers/ChatController.cs` — file cập nhật đầy đủ:

```csharp
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Options;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/chat")]
public class ChatController(ChatService chat, IOptions<ChatOptions> options) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Chat([FromBody] ChatRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        try
        {
            var result = await chat.ChatAsync(req, ct);
            return Ok(result);
        }
        catch (KeyNotFoundException ex)
        {
            // Session ID was provided but does not exist (e.g. expired by cleanup job).
            return NotFound(new { detail = ex.Message, code = "SESSION_NOT_FOUND" });
        }
    }

    /// <summary>
    /// GET /api/chat/{sessionId}/history?page=1&amp;pageSize=50
    ///
    /// Returns messages in ascending chronological order.
    /// page is 1-based. pageSize is capped at 200 to prevent runaway queries.
    /// </summary>
    [HttpGet("{sessionId:guid}/history")]
    public async Task<IActionResult> GetHistory(
        Guid sessionId,
        [FromQuery] int page = 1,
        [FromQuery] int pageSize = 0,
        CancellationToken ct = default)
    {
        var defaultPageSize = options.Value.HistoryPageSize;

        if (page < 1) page = 1;
        if (pageSize <= 0) pageSize = defaultPageSize;
        if (pageSize > 200) pageSize = 200;   // Hard cap — prevent runaway queries.

        var history = await chat.GetHistoryAsync(sessionId, page, pageSize, ct);
        if (history is null)
            return NotFound(new { detail = "Session not found", code = "SESSION_NOT_FOUND" });

        return Ok(history);
    }

    [HttpDelete("{sessionId:guid}")]
    public async Task<IActionResult> DeleteSession(Guid sessionId, CancellationToken ct = default)
    {
        await chat.DeleteSessionAsync(sessionId, ct);
        return Ok(new StatusResponse("ok", "Session deleted"));
    }
}
```

---

### 3.7 Thay đổi Frontend (`ChatTab.vue` + `api/index.ts`)

#### Vấn đề

`ChatTab.vue` có một đường dẫn xử lý session hết hạn hiện có trong `onMounted` (block `catch` xóa `localStorage`). Cần thêm ba phần:

1. **Phục hồi phiên hết hạn khi POST** — nếu `POST /api/chat` trả về 404 với `code: "SESSION_NOT_FOUND"`, tự động bắt đầu một phiên mới thay vì hiển thị lỗi không rõ ràng.
2. **Chỉ báo sử dụng context** — hiển thị banner khi `ContextStats.IsTruncated` là true, để người dùng biết LLM không thấy toàn bộ cuộc hội thoại.
3. **Phân trang load-more cho lịch sử** — endpoint lịch sử giờ trả về theo trang; UI nên cung cấp nút "Tải tin nhắn cũ hơn".

#### `frontend/src/api/index.ts` — cập nhật types và functions

Thêm/thay thế các section này trong file hiện có:

```typescript
// Replace the existing ChatHistoryResponse interface:
export interface ContextUsageStats {
  messagesInContext: number
  totalMessages: number
  maxMessagesInContext: number
  isTruncated: boolean
}

export interface ChatResponse {
  sessionId: string
  answer?: string
  citations?: number[]
  chunks: ChunkResult[]
  contextStats?: ContextUsageStats
}

export interface ChatHistoryResponse {
  sessionId: string
  messages: { role: string; content: string }[]
  page: number
  pageSize: number
  totalMessages: number
  totalPages: number
  hasNextPage: boolean
  hasPreviousPage: boolean
}

// Replace the existing getChatHistory function:
export const getChatHistory = (sessionId: string, page = 1, pageSize = 50) =>
  api.get<ChatHistoryResponse>(`/chat/${sessionId}/history`, {
    params: { page, pageSize },
  })
```

#### `frontend/src/components/ChatTab.vue` — các thay đổi logic quan trọng

Toàn bộ section `<script setup>` được cập nhật:

```typescript
<script setup lang="ts">
import { ref, nextTick, onMounted, computed } from 'vue'
import {
  chat, getChatHistory, deleteChatSession,
  type ChunkResult, type ChatHistoryResponse, type ContextUsageStats,
} from '../api'
import { useCollectionsStore } from '../stores/collections'

const store = useCollectionsStore()
const collection = ref('documents')
const useReranker = ref(false)
const searchMode = ref('semantic')

const SESSION_KEY = 'openrag_chat_session_id'

interface Message {
  role: 'user' | 'assistant'
  content: string
  chunks?: ChunkResult[]
  citations?: number[]
  showSources?: boolean
}

const sessionId = ref<string | null>(localStorage.getItem(SESSION_KEY))
const messages = ref<Message[]>([])
const query = ref('')
const loading = ref(false)
const error = ref('')
const messagesEl = ref<HTMLElement | null>(null)

// Trạng thái phân trang cho lịch sử
const historyPage = ref(1)
const historyTotalPages = ref(1)
const loadingHistory = ref(false)
const hasEarlierMessages = computed(() => historyPage.value < historyTotalPages.value)

// Thống kê sử dụng context — được lấy từ ChatResponse cuối cùng
const contextStats = ref<ContextUsageStats | null>(null)

// ── Session bootstrap ─────────────────────────────────────────────────────

onMounted(async () => {
  if (sessionId.value) {
    await loadHistory(sessionId.value, 1)
  }
})

async function loadHistory(sid: string, page: number) {
  loadingHistory.value = true
  try {
    const { data } = await getChatHistory(sid, page)
    const incoming = data.messages.map(m => ({
      role: m.role as 'user' | 'assistant',
      content: m.content,
    }))

    if (page === 1) {
      messages.value = incoming
    } else {
      // Prepend older messages (page > 1 goes further back in time)
      messages.value = [...incoming, ...messages.value]
    }

    historyPage.value = data.page
    historyTotalPages.value = data.totalPages
  } catch {
    // Session has been deleted (expired) — start fresh
    clearSession()
  } finally {
    loadingHistory.value = false
  }
}

async function loadEarlierMessages() {
  if (!sessionId.value || !hasEarlierMessages.value) return
  await loadHistory(sessionId.value, historyPage.value + 1)
}

// ── Send ──────────────────────────────────────────────────────────────────

async function sendMessage() {
  if (!query.value.trim() || loading.value) return
  const userQuery = query.value
  query.value = ''

  messages.value.push({ role: 'user', content: userQuery })
  loading.value = true
  error.value = ''
  await scrollToBottom()

  try {
    const { data } = await chat({
      query: userQuery,
      collection: collection.value,
      sessionId: sessionId.value ?? undefined,
      topK: 5,
      useReranker: useReranker.value,
      searchMode: searchMode.value,
    })

    sessionId.value = data.sessionId
    localStorage.setItem(SESSION_KEY, data.sessionId)
    contextStats.value = data.contextStats ?? null

    messages.value.push({
      role: 'assistant',
      content: data.answer ?? '*(Không có câu trả lời — LLM chưa được cấu hình)*',
      chunks: data.chunks,
      citations: data.citations ?? [],
      showSources: false,
    })
  } catch (e: any) {
    const code = e.response?.data?.code
    if (e.response?.status === 404 && code === 'SESSION_NOT_FOUND') {
      // Session was expired by the cleanup job — transparently start a new one.
      clearSession()
      error.value = 'Phiên hội thoại đã hết hạn. Đang bắt đầu phiên mới...'
      // Re-send the same query in a fresh session
      query.value = userQuery
    } else {
      error.value = e.response?.data?.detail ?? e.message
      messages.value.push({ role: 'assistant', content: `*Lỗi: ${error.value}*` })
    }
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

function clearSession() {
  sessionId.value = null
  localStorage.removeItem(SESSION_KEY)
  messages.value = []
  contextStats.value = null
  historyPage.value = 1
  historyTotalPages.value = 1
}

function newConversation() {
  if (sessionId.value) {
    deleteChatSession(sessionId.value).catch(() => {})
  }
  clearSession()
  error.value = ''
}

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value)
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

function renderAnswer(text: string) {
  return text.replace(/\[(\d+)\]/g,
    '<span class="inline-flex items-center justify-center w-5 h-5 text-xs bg-violet-600 text-white rounded-full font-bold mx-0.5">$1</span>')
}

function scoreColor(score: number) {
  if (score >= 0.5) return 'text-green-400'
  if (score >= 0.3) return 'text-yellow-400'
  return 'text-red-400'
}
</script>
```

**Các bổ sung vào Template** — ba block mới cần được chèn vào template hiện có:

1. **Nút "Tải tin nhắn cũ hơn"** — chèn vào đầu div danh sách tin nhắn, trước `<div v-if="!messages.length"...>` hiển thị trạng thái rỗng:

```html
<!-- Load earlier messages (pagination) -->
<div v-if="hasEarlierMessages" class="flex justify-center py-2">
  <button
    @click="loadEarlierMessages"
    :disabled="loadingHistory"
    class="text-xs text-slate-400 hover:text-violet-400 border border-slate-600 rounded px-3 py-1 transition-colors disabled:opacity-50"
  >
    {{ loadingHistory ? 'Đang tải...' : 'Tải tin nhắn cũ hơn' }}
  </button>
</div>
```

2. **Cảnh báo context bị cắt bớt** — chèn giữa danh sách tin nhắn và thanh nhập liệu:

```html
<!-- Context window truncation notice -->
<div
  v-if="contextStats?.isTruncated"
  class="flex-shrink-0 flex items-center gap-2 px-3 py-1.5 mb-2 rounded-lg bg-amber-900/20 border border-amber-700/40 text-xs text-amber-400"
>
  <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
      d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
  </svg>
  LLM chỉ thấy {{ contextStats.messagesInContext }} / {{ contextStats.totalMessages }} tin nhắn gần nhất.
  <span class="text-amber-500 font-medium">Lịch sử bị cắt bớt.</span>
</div>
```

3. **Lỗi inline phiên hết hạn** (thay thế push `error.value` hiện có bằng một banner hiển thị cho người dùng sử dụng `v-if="error"` đã có sẵn hoặc thêm mới):

Màn hình hiển thị lỗi hiện có trong template (nếu có) đã được hiển thị. Thay đổi quan trọng nằm ở `sendMessage` phía trên: đường dẫn `SESSION_NOT_FOUND` tái đặt lại `query.value` và gọi `clearSession()` để người dùng có thể đơn giản nhấn Gửi lại trong một phiên mới.

---

## 4. Chiến lược Migration (Zero-Downtime)

### Trình tự thực hiện

```
Bước 1  Triển khai chỉ migration (dotnet ef database update)
        — Binary cũ chạy, bỏ qua cột LastActivityAt.
        — LastActivityAt được backfill từ UpdatedAt qua DEFAULT expression.
        — Không có downtime.

Bước 2  Triển khai binary mới
        — ChatService giờ ghi LastActivityAt mỗi request.
        — CleanupService khởi động, chờ CleanupIntervalHours trước lần chạy đầu tiên.
        — Các phiên cũ không có hoạt động kể từ Bước 1 sẽ được dọn dẹp
          dựa trên LastActivityAt được backfill từ UpdatedAt — đúng đắn.

Bước 3  Theo dõi
        — Xem log "ChatCleanupService: deleted N expired session(s)".
        — Xác nhận kích thước file DB giảm (hoặc ngừng tăng) theo thời gian.
        — Nếu SessionTtlDays quá ngắn, tăng lên trong appsettings
          mà không cần redeploy (IOptionsMonitor có thể dùng cho hot-reload;
          IOptions là đủ nếu rolling restart được chấp nhận).
```

### Rollback

Nếu binary mới phải được rollback:

- Binary cũ chạy trên schema mới nhưng bỏ qua `LastActivityAt` — hoạt động chính xác.
- `CleanupService` không có trong binary cũ — không có việc dọn dẹp nào chạy, dữ liệu an toàn.
- Chạy lại `dotnet ef database update <previous-migration-name>` nếu cột phải được xóa; điều này an toàn với SQLite vì nó tạo lại bảng.

---

## 5. Tóm tắt đầy đủ các file thay đổi

| File | Loại thay đổi | Mô tả |
|---|---|---|
| `appsettings.json` | Sửa | Thêm section cấu hình `Chat` |
| `Options/ChatOptions.cs` | **Mới** | Class options strongly-typed |
| `Models/Entities/ChatSession.cs` | Sửa | Thêm property `LastActivityAt` |
| `Data/AppDbContext.cs` | Sửa | Thêm cấu hình index `ChatSession`; thay thế index `ChatMessage` |
| `Data/Migrations/YYYYMMDDHHMMSS_AddSessionTtlSupport.cs` | **Mới** | Migration: cột `LastActivityAt` + indexes |
| `Services/ChatCleanupService.cs` | **Mới** | `BackgroundService` dọn dẹp TTL |
| `Services/ChatService.cs` | Sửa | Query sliding window; cập nhật `LastActivityAt`; lịch sử có phân trang |
| `Models/Dto/Responses/ChatResponse.cs` | Sửa | Thêm `ContextUsageStats`; cập nhật `ChatHistoryResponse` |
| `Controllers/ChatController.cs` | Sửa | Tham số query `page`/`pageSize`; code `SESSION_NOT_FOUND` |
| `Program.cs` | Sửa | Đăng ký `ChatOptions`; đăng ký `ChatCleanupService` |
| `frontend/src/api/index.ts` | Sửa | Cập nhật types; tham số phân trang `getChatHistory` |
| `frontend/src/components/ChatTab.vue` | Sửa | Phục hồi phiên; banner context; nút load-earlier |

---

## 6. Đánh đổi thiết kế

### 6.1 SQLite so với RDBMS thực sự

`ExecuteDeleteAsync` trên SQLite không phải là atomic trên nhiều bảng khi cascade delete dựa vào trigger — `ON DELETE CASCADE` của SQLite được triển khai dưới dạng trigger và kích hoạt đúng cách, nhưng toàn bộ thao tác nằm trong một transaction ngầm định duy nhất, vì vậy nó an toàn. Nếu dự án chuyển sang PostgreSQL, cùng code EF Core hoạt động không cần thay đổi.

### 6.2 Sliding window so với tóm tắt hóa

Lấy N tin nhắn cuối là chiến lược đơn giản nhất. Một giải pháp thay thế cấp production là **rolling summarisation**: khi số tin nhắn vượt quá giới hạn, gọi LLM để tóm tắt phần cũ hơn, lưu tóm tắt dưới dạng tin nhắn role `"summary"` đặc biệt, và thêm nó vào đầu context tương lai. Điều này bảo toàn thông tin ngữ nghĩa nhưng yêu cầu một LLM call bổ sung mỗi khi vượt ngưỡng và làm phức tạp DB schema. Sliding window là điểm khởi đầu đúng đắn; việc tóm tắt hóa có thể được bổ sung sau mà không phá vỡ API contract.

### 6.3 `IOptions` so với `IOptionsMonitor`

`IOptions<ChatOptions>` được inject một lần khi khởi tạo service. Thay đổi `Chat:SessionTtlDays` trong `appsettings.json` yêu cầu khởi động lại tiến trình. Sử dụng `IOptionsMonitor<ChatOptions>` sẽ cho phép hot-reload mà không cần restart. Background service nên dùng `IOptionsMonitor` và gọi `.CurrentValue` trong mỗi vòng lặp nếu hot-reload là mong muốn. Thiết kế hiện tại dùng `IOptions` cho đơn giản; con đường nâng cấp rất đơn giản (thay thế kiểu injection và pattern truy cập `.Value`).

### 6.4 `BackgroundService` so với job ngoài dựa trên cron

`BackgroundService` chạy trong tiến trình, có nghĩa là:

- **Ưu điểm**: không phụ thuộc vào scheduler ngoài, tham gia vào graceful shutdown, chia sẻ DI container.
- **Nhược điểm**: không chạy trong các khoảng thời gian deployment; nếu API không có uptime qua đêm, việc dọn dẹp không xảy ra. Đối với độ tin cậy rất cao, hãy ủy quyền dọn dẹp cho external scheduler (OS cron, Kubernetes CronJob, Hangfire). Với quy mô của dự án này, `BackgroundService` là phù hợp.

### 6.5 Giới hạn cứng 200 cho `pageSize`

Controller bắt buộc `pageSize = Math.Min(pageSize, 200)`. Điều này ngăn client yêu cầu `pageSize=100000` và tạo toàn bộ bảng trong một query. Sự đánh đổi là các phiên rất dài yêu cầu nhiều round-trip để hydrate đầy đủ trong UI — chấp nhận được vì UI sử dụng load-more dần dần thay vì tải tất cả cùng một lúc.

### 6.6 `COUNT` + `SELECT` mỗi chat request (hai query)

`ChatAsync` giờ thực hiện hai query: một `COUNT(*)` và một `SELECT TOP N`. Đối với SQLite trên deployment một server này là không đáng kể (dưới mili giây). Nếu điều này trở thành bottleneck, count có thể được cache trong cột denormalized `ChatSession.MessageCount`, tăng lên khi insert — nhưng điều đó tạo ra rủi ro tính nhất quán và là tối ưu hóa sớm cho quy mô hiện tại.
