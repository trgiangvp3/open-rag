# Design: Bounded Chat History & Session TTL

**File**: `plans/design-chat-history-ttl.md`
**Status**: Design proposal — not yet implemented
**Date**: 2026-03-26

---

## 1. Problem Statement

`ChatService.ChatAsync()` loads every historical message for a session via EF Core's eager-load (`.Include(s => s.Messages)`). There is no cleanup, no cap, and no pagination. This produces four compounding failure modes:

| Failure | Root cause | Observed symptom |
|---|---|---|
| DB grows unbounded | No session or message TTL | SQLite file size grows forever |
| Slow session load | Full table scan over all messages for a session | Request latency increases with session age |
| Silent LLM truncation | All history passed raw to the LLM API | Older turns are silently cut by the model's context window; citations break |
| No user visibility | No token/message count surfaced to the frontend | Users cannot tell when their history is being ignored |

The `UpdatedAt` column on `ChatSession` is the only activity signal, but it is never queried for cleanup and has no index. There are currently three migrations (`InitialCreate`, `AddChatSessions`, `AddPerformanceIndexes`) producing the current schema.

---

## 2. Solution Overview

Six coordinated changes:

1. **Session TTL** — `BackgroundService` that periodically deletes sessions idle for > N days.
2. **Message sliding window** — `ChatService` sends only the last N messages to the LLM, never the full history.
3. **Paginated history API** — `GET /api/chat/{sessionId}/history?page=1&pageSize=50` with total count and page metadata.
4. **DB migration** — `LastActivityAt` column + index on `ChatSessions`; composite index on `ChatMessages(SessionId, CreatedAt)`.
5. **Configuration** — All thresholds in `appsettings.json` under `Chat:*`, bound via `IOptions<ChatOptions>`.
6. **Frontend** — Expired-session detection, per-turn context usage indicator, load-more pagination.

---

## 3. Sub-Solution Detail

### 3.1 Configuration (`appsettings.json` + Options class)

#### `OpenRAG.Api/appsettings.json` — add `Chat` section

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

**Semantics**:

- `SessionTtlDays` — sessions with no activity for this many days are deleted. `0` disables cleanup entirely.
- `MaxMessagesInContext` — maximum number of messages sent to the LLM per request. Applied as a tail-of-history sliding window.
- `CleanupIntervalHours` — how often the background cleanup job runs.
- `HistoryPageSize` — default page size for the paginated history endpoint.

#### New file: `OpenRAG.Api/Options/ChatOptions.cs`

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

#### Why `LastActivityAt` instead of reusing `UpdatedAt`

`UpdatedAt` is semantically correct but has no index and its value is set via `session.UpdatedAt = DateTime.UtcNow` scattered in code rather than centralised. Renaming it would be a breaking column rename in SQLite (which requires table recreation). Instead, add a dedicated indexed column `LastActivityAt` that the background job queries exclusively, and keep `UpdatedAt` for audit purposes.

#### New migration: `OpenRAG.Api/Data/Migrations/YYYYMMDDHHMMSS_AddSessionTtlSupport.cs`

(Run `dotnet ef migrations add AddSessionTtlSupport` after applying the model changes below, then replace the auto-generated body with:)

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

> **Zero-downtime note**: SQLite's `ALTER TABLE ADD COLUMN` with a `DEFAULT` is non-blocking. The old binary can still run against the new schema — it simply ignores `LastActivityAt`. The new binary writes it. There is a window where sessions from the old binary have `LastActivityAt = UpdatedAt` (backfilled by the default), which is correct. No table lock, no data loss.

#### `AppDbContext.OnModelCreating` changes

File: `OpenRAG.Api/Data/AppDbContext.cs`

Replace the `ChatMessageEntity` and add the `ChatSession` configuration block:

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

#### `ChatSession` entity update

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

#### New file: `OpenRAG.Api/Services/ChatCleanupService.cs`

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

**Key design decisions**:

- `IServiceScopeFactory` + `CreateAsyncScope()` — `AppDbContext` is registered as `Scoped`. `BackgroundService` is `Singleton`, so it cannot inject `AppDbContext` directly. A new scope per run avoids the anti-pattern of a long-lived DbContext.
- `ExecuteDeleteAsync` — EF Core 7+ bulk-delete. Translates to `DELETE FROM ChatSessions WHERE LastActivityAt < ?`. No objects are materialised in memory; cascade at the DB level removes messages.
- Guard on `SessionTtlDays <= 0` — operators can disable cleanup without removing the service registration.
- The `catch` swallows transient errors rather than crashing the host process.

#### Register in `Program.cs`

Add after the existing service registrations:

```csharp
// ── Chat configuration ────────────────────────────────────────────────────
builder.Services.Configure<ChatOptions>(
    builder.Configuration.GetSection(ChatOptions.SectionName));

// ── Background services ───────────────────────────────────────────────────
builder.Services.AddHostedService<ChatCleanupService>();
```

---

### 3.4 Message Sliding Window in `ChatService`

The change is in the history-building step (step 3) of `ChatAsync`. Instead of loading all messages via `.Include()`, query only the last N directly from the DB ordered by `CreatedAt DESC LIMIT N`, then reverse for chronological order. This avoids materialising thousands of messages just to take the tail.

Additionally, `LastActivityAt` must be set on every interaction.

File: `OpenRAG.Api/Services/ChatService.cs` — complete updated file:

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

**Why two queries instead of Include + LINQ Take?**

`.Include(s => s.Messages).Take(N)` does not work as expected: EF Core applies `Take` to the sessions query, not to the included messages collection. You would load all messages and apply `Take` in memory. The explicit `.Where(m => m.SessionId == ...).OrderByDescending(...).Take(N)` generates correct SQL.

---

### 3.5 Response DTOs

File: `OpenRAG.Api/Models/Dto/Responses/ChatResponse.cs` — complete updated file:

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

### 3.6 Controller Updates

File: `OpenRAG.Api/Controllers/ChatController.cs` — complete updated file:

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

### 3.7 Frontend Changes (`ChatTab.vue` + `api/index.ts`)

#### Problem

`ChatTab.vue` has one existing session-expiry path in `onMounted` (the `catch` block clears `localStorage`). It needs three additions:

1. **Expired-session recovery on POST** — if `POST /api/chat` returns 404 with `code: "SESSION_NOT_FOUND"`, start a new session automatically rather than showing an opaque error.
2. **Context usage indicator** — show a banner when `ContextStats.IsTruncated` is true, so users know the LLM is not seeing the full conversation.
3. **Load-more pagination** for history — the history endpoint now returns pages; the UI should offer a "Load earlier messages" button.

#### `frontend/src/api/index.ts` — updated types and functions

Add/replace these sections in the existing file:

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

#### `frontend/src/components/ChatTab.vue` — key logic changes

The full updated `<script setup>` section:

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

// Pagination state for history
const historyPage = ref(1)
const historyTotalPages = ref(1)
const loadingHistory = ref(false)
const hasEarlierMessages = computed(() => historyPage.value < historyTotalPages.value)

// Context usage — populated from the last ChatResponse
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

**Template additions** — three new blocks to splice into the existing template:

1. **"Load earlier messages" button** — insert at the top of the message list div, before the empty-state `<div v-if="!messages.length"...>`:

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

2. **Context truncation warning** — insert between the message list and input bar:

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

3. **Session expiry inline error** (replace the existing `error.value` push with a user-visible banner using `v-if="error"` already present or add):

The existing error display in the template (if any) is already shown. The key change is in `sendMessage` above: the `SESSION_NOT_FOUND` path repopulates `query.value` and calls `clearSession()` so the user can simply press Send again in a fresh session.

---

## 4. Migration Strategy (Zero-Downtime)

### Sequence

```
Step 1  Deploy migration only (dotnet ef database update)
        — Old binary runs, ignores LastActivityAt column.
        — LastActivityAt backfilled from UpdatedAt via DEFAULT expression.
        — No downtime.

Step 2  Deploy new binary
        — ChatService now writes LastActivityAt on every request.
        — CleanupService starts, waits CleanupIntervalHours before first run.
        — Old sessions without activity since Step 1 will be cleaned
          based on their UpdatedAt-backfilled LastActivityAt — correct.

Step 3  Monitor
        — Watch logs for "ChatCleanupService: deleted N expired session(s)".
        — Confirm DB file size decreases (or stops growing) over days.
        — If SessionTtlDays is too aggressive, increase it in appsettings
          without redeployment (IOptionsMonitor can be used for hot-reload;
          IOptions is sufficient if a rolling restart is acceptable).
```

### Rollback

If the new binary must be rolled back:

- Old binary runs against the new schema but ignores `LastActivityAt` — works correctly.
- `CleanupService` is not present in the old binary — no cleanup runs, data is safe.
- Re-run `dotnet ef database update <previous-migration-name>` if the column must be dropped; this is safe with SQLite as it recreates the table.

---

## 5. Complete File Change Summary

| File | Change type | Description |
|---|---|---|
| `appsettings.json` | Edit | Add `Chat` config section |
| `Options/ChatOptions.cs` | **New** | Strongly-typed options class |
| `Models/Entities/ChatSession.cs` | Edit | Add `LastActivityAt` property |
| `Data/AppDbContext.cs` | Edit | Add `ChatSession` index config; replace `ChatMessage` index |
| `Data/Migrations/YYYYMMDDHHMMSS_AddSessionTtlSupport.cs` | **New** | Migration: `LastActivityAt` column + indexes |
| `Services/ChatCleanupService.cs` | **New** | `BackgroundService` TTL cleanup |
| `Services/ChatService.cs` | Edit | Sliding window query; `LastActivityAt` update; paginated history |
| `Models/Dto/Responses/ChatResponse.cs` | Edit | Add `ContextUsageStats`; update `ChatHistoryResponse` |
| `Controllers/ChatController.cs` | Edit | `page`/`pageSize` query params; `SESSION_NOT_FOUND` code |
| `Program.cs` | Edit | Register `ChatOptions`; register `ChatCleanupService` |
| `frontend/src/api/index.ts` | Edit | Updated types; `getChatHistory` pagination params |
| `frontend/src/components/ChatTab.vue` | Edit | Session recovery; context banner; load-earlier button |

---

## 6. Trade-offs

### 6.1 SQLite vs. a proper RDBMS

`ExecuteDeleteAsync` on SQLite is not atomic across multiple tables when cascade delete relies on triggers — SQLite's `ON DELETE CASCADE` is implemented as a trigger and fires correctly, but the entire operation is inside a single implicit transaction, so it is safe. If the project migrates to PostgreSQL, the same EF Core code works unchanged.

### 6.2 Sliding window vs. summarisation

Taking the last N messages is the simplest strategy. A production-grade alternative is **rolling summarisation**: when message count exceeds the cap, call the LLM to summarise the older portion, store the summary as a special `"summary"` role message, and prepend it to future context. This preserves semantic information but requires an extra LLM call per threshold crossing and complicates the DB schema. The sliding window is the correct starting point; summarisation can be layered on top later without breaking the API contract.

### 6.3 `IOptions` vs. `IOptionsMonitor`

`IOptions<ChatOptions>` is injected once at service construction. Changing `Chat:SessionTtlDays` in `appsettings.json` requires a process restart. Using `IOptionsMonitor<ChatOptions>` would allow hot-reload without restart. The background service should use `IOptionsMonitor` and call `.CurrentValue` on each iteration if hot-reload is desired. The current design uses `IOptions` for simplicity; the upgrade path is straightforward (replace the injection type and `.Value` access pattern).

### 6.4 `BackgroundService` vs. a cron-based external job

`BackgroundService` runs in-process, which means:
- **Pros**: no external scheduler dependency, participates in graceful shutdown, shares the DI container.
- **Cons**: does not run during deployment gaps; if the API has zero uptime overnight, cleanup does not happen. For very high reliability, delegate cleanup to an external scheduler (OS cron, Kubernetes CronJob, Hangfire). For this project's scale, `BackgroundService` is proportionate.

### 6.5 Hard cap of 200 on `pageSize`

The controller enforces `pageSize = Math.Min(pageSize, 200)`. This prevents a client from requesting `pageSize=100000` and materialising the entire table in one query. The trade-off is that very long sessions require multiple round-trips to fully hydrate in the UI — acceptable since the UI uses progressive load-more rather than loading everything at once.

### 6.6 `COUNT` + `SELECT` per chat request (two queries)

`ChatAsync` now executes two queries: one `COUNT(*)` and one `SELECT TOP N`. For SQLite on a single-server deployment this is negligible (sub-millisecond). If this becomes a bottleneck, the count can be cached in a `ChatSession.MessageCount` denormalised column, incremented on insert — but that introduces a consistency risk and is premature optimisation for the current scale.
