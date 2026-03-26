# ML Service Resilience — Thiết Kế Circuit Breaker Pattern

**Trạng thái**: Đề xuất thiết kế
**Mục tiêu**: OpenRAG.Api (.NET 8)
**Ngày**: 2026-03-26

---

## Mục Lục

1. [Mô Tả Vấn Đề](#1-mô-tả-vấn-đề)
2. [Tổng Quan Giải Pháp](#2-tổng-quan-giải-pháp)
3. [NuGet Packages](#3-nuget-packages)
4. [Polly Circuit Breaker — Cấu Hình Program.cs](#4-polly-circuit-breaker--cấu-hình-programcs)
5. [MlClient — Wrapper Xử Lý Ngoại Lệ](#5-mlclient--wrapper-xử-lý-ngoại-lệ)
6. [Graceful Degradation Trong Controllers](#6-graceful-degradation-trong-controllers)
7. [Health Check Endpoint](#7-health-check-endpoint)
8. [Document Retry Background Service](#8-document-retry-background-service)
9. [Frontend UX (Vue)](#9-frontend-ux-vue)
10. [Cấu Hình (appsettings.json)](#10-cấu-hình-appsettingsjson)
11. [Lưu Ý Khi Triển Khai](#11-lưu-ý-khi-triển-khai)

---

## 1. Mô Tả Vấn Đề

Python ML service (`http://localhost:8001`) là một phụ thuộc cứng cho tất cả các thao tác chính của OpenRAG. Khi service này không khả dụng, các lỗi lan truyền theo chuỗi sau sẽ xảy ra:

| Tình huống | Hành vi hiện tại | Tác động |
|---|---|---|
| ML service bị ngắt | `HttpRequestException` lan truyền mà không được bắt | HTTP 500 không có thông báo lỗi rõ ràng cho người dùng |
| ML service chậm / quá tải | Mỗi request giữ một kết nối lên đến **300 giây** | Cạn kiệt thread pool dưới tải nặng |
| ML service khởi động lại | Tất cả request đang xử lý thất bại ngay lập tức | Người dùng mất dữ liệu, phải upload lại |
| ML liên tục thất bại | Không có circuit break — mỗi lần gọi vẫn cố kết nối trực tiếp | Thundering herd, service không thể phục hồi |
| Upload thất bại giữa chừng khi đang index | `Document.Status = "failed"` không có cơ chế thử lại | Documents bị kẹt vĩnh viễn ở trạng thái failed/indexing |

### Các vị trí nguyên nhân gốc rễ

- `OpenRAG.Api/Program.cs` dòng 15–19: `HttpClient.Timeout = 300 s`, không có pipeline resilience.
- `OpenRAG.Api/Services/MlClient.cs`: mọi phương thức đều gọi `EnsureSuccessStatusCode()` mà không bắt `HttpRequestException` hay `BrokenCircuitException`.
- `OpenRAG.Api/Controllers/SearchController.cs` và `ChatService.cs`: không có try/catch xung quanh `ml.SearchAsync()`.
- `OpenRAG.Api/Controllers/DocumentsController.cs`: trả về exception 500 trực tiếp về client.
- Không có `BackgroundService` để thử lại các documents bị kẹt ở trạng thái `"failed"` hoặc `"indexing"`.

---

## 2. Tổng Quan Giải Pháp

```
┌─────────────────────────────────────────────────────────────────┐
│  .NET 8 API                                                     │
│                                                                 │
│  Controllers ──► DocumentService / ChatService / Search        │
│                           │                                     │
│                    try { ml.Xxx() }                             │
│                    catch (BrokenCircuitException)               │
│                          └─► HTTP 503 + structured JSON         │
│                                                                 │
│  MlClient ──► HttpClient (with Polly pipeline)                  │
│                  ├─ Timeout:       30 s per attempt             │
│                  ├─ Retry:         3× exponential (1 s, 2 s, 4 s)│
│                  └─ CircuitBreaker: open after 5 fails / 30 s  │
│                                    half-open after 60 s         │
│                                                                 │
│  /api/health ──► MlServiceHealthCheck (IHealthCheck)           │
│                                                                 │
│  DocumentRetryService (BackgroundService)                       │
│       └─ every 5 min: retry documents with status "failed"     │
│          or stuck in "indexing" > 10 min                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. NuGet Packages

Dự án đã có `Microsoft.Extensions.Http.Resilience` 10.4.0 trong `OpenRAG.Api.csproj`. Package này cung cấp tích hợp Polly v8 đầy đủ thông qua `AddStandardResilienceHandler()` và `AddResilienceHandler()`. Không cần thêm package nào khác.

```xml
<!-- OpenRAG.Api/OpenRAG.Api.csproj — đã có sẵn, không cần thay đổi -->
<PackageReference Include="Microsoft.Extensions.Http.Resilience" Version="10.4.0" />

<!-- Health checks UI (tuỳ chọn, cho /health endpoint với JSON đầy đủ) -->
<PackageReference Include="Microsoft.Extensions.Diagnostics.HealthChecks" Version="8.0.*" />
```

`Microsoft.Extensions.Diagnostics.HealthChecks` được tích hợp sẵn trong .NET 8 SDK meta-package, do đó không cần cài riêng cho các extension method health check (`AddHealthChecks()`, `MapHealthChecks()`).

---

## 4. Polly Circuit Breaker — Cấu Hình Program.cs

### 4.1 Chiến lược

Dùng `AddResilienceHandler()` (từ `Microsoft.Extensions.Http.Resilience`) để gắn một Polly pipeline có tên vào `HttpClient` của `MlClient`. Cách này được ưu tiên hơn `AddStandardResilienceHandler()` vì các giá trị mặc định của handler chuẩn (đặc biệt là ngưỡng circuit-breaker tích cực) có thể xung đột với các lần gọi embedding dài của ML service.

Ba chiến lược được lồng theo thứ tự (ngoài → trong):

1. **Timeout** — 30 giây mỗi lần thử riêng lẻ (bắt các lần gọi bị treo trước khi retry).
2. **Retry** — 3 lần thử thêm (tổng 4 lần) với exponential backoff: 1 s → 2 s → 4 s. Chỉ retry các lỗi HTTP tạm thời (5xx, 408, network failures).
3. **Circuit Breaker** — dựa trên sampling: mở sau ≥ 5 lần thất bại trong cửa sổ 30 giây (tỉ lệ thất bại ≥ 50%). Giữ mở 60 giây; sau đó half-open để cho một probe request qua.

### 4.2 `Program.cs` đầy đủ

**File**: `OpenRAG.Api/Program.cs`

```csharp
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Chunking;
using OpenRAG.Api.Services.HealthChecks;
using OpenRAG.Api.Services.Background;
using Polly;
using Polly.CircuitBreaker;
using Polly.Retry;
using Polly.Timeout;
using System.Net;

var builder = WebApplication.CreateBuilder(args);

// ── Database ──────────────────────────────────────────────────────────────
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString("Default")
        ?? "Data Source=../data/openrag.db"));

// ── ML Service HTTP client với Polly resilience pipeline ──────────────────
builder.Services.AddHttpClient<MlClient>(client =>
{
    client.BaseAddress = new Uri(
        builder.Configuration["MlService:BaseUrl"] ?? "http://localhost:8001");

    // Bỏ blanket timeout 300 s — per-attempt timeout giờ được quản lý bởi
    // Polly TimeoutStrategy bên dưới. Đặt HttpClient timeout cao hơn một chút
    // so với tổng thời gian tối đa (per-attempt 30 s × 4 lần + 3 backoff delays
    // tối đa 7 s = ~127 s). Dùng 150 s làm giới hạn an toàn.
    client.Timeout = TimeSpan.FromSeconds(150);
})
.AddResilienceHandler("ml-resilience", pipeline =>
{
    // ── 1. Per-attempt timeout (trong cùng — được đánh giá đầu tiên trong mỗi lần thử) ──
    pipeline.AddTimeout(new HttpTimeoutStrategyOptions
    {
        Timeout = TimeSpan.FromSeconds(
            builder.Configuration.GetValue("MlService:Resilience:AttemptTimeoutSeconds", 30)),
        Name = "ml-attempt-timeout",
    });

    // ── 2. Retry với exponential back-off ─────────────────────────────────
    pipeline.AddRetry(new HttpRetryStrategyOptions
    {
        MaxRetryAttempts = builder.Configuration.GetValue("MlService:Resilience:MaxRetryAttempts", 3),
        BackoffType = DelayBackoffType.Exponential,
        Delay = TimeSpan.FromSeconds(1),   // 1 s, 2 s, 4 s
        UseJitter = true,                   // tránh thundering-herd khi retry đồng thời
        Name = "ml-retry",
        // Retry khi gặp lỗi HTTP tạm thời và timeout nhưng KHÔNG retry lỗi 4xx từ client
        ShouldHandle = new PredicateBuilder<HttpResponseMessage>()
            .Handle<HttpRequestException>()
            .Handle<TimeoutRejectedException>()
            .HandleResult(r => r.StatusCode is
                HttpStatusCode.ServiceUnavailable or
                HttpStatusCode.GatewayTimeout or
                HttpStatusCode.RequestTimeout or
                HttpStatusCode.BadGateway or
                HttpStatusCode.InternalServerError),
        OnRetry = args =>
        {
            var logger = args.Context.ServiceProvider
                .GetRequiredService<ILogger<MlClient>>();
            logger.LogWarning(
                "ML service retry attempt {Attempt} after {Delay:N1} s — outcome: {Outcome}",
                args.AttemptNumber + 1,
                args.RetryDelay.TotalSeconds,
                args.Outcome.Exception?.Message ?? args.Outcome.Result?.StatusCode.ToString());
            return ValueTask.CompletedTask;
        },
    });

    // ── 3. Circuit breaker (ngoài cùng — kích hoạt sau nhiều lần thất bại) ──
    pipeline.AddCircuitBreaker(new HttpCircuitBreakerStrategyOptions
    {
        // Cửa sổ sampling: theo dõi kết quả trong 30 giây
        SamplingDuration = TimeSpan.FromSeconds(
            builder.Configuration.GetValue("MlService:Resilience:SamplingWindowSeconds", 30)),
        // Mở circuit nếu ≥ 50% lần gọi thất bại VÀ đã có ít nhất 5 lần gọi
        FailureRatio = builder.Configuration.GetValue("MlService:Resilience:FailureRatio", 0.5),
        MinimumThroughput = builder.Configuration.GetValue("MlService:Resilience:MinimumThroughput", 5),
        // Giữ mở (từ chối ngay lập tức) trong 60 giây
        BreakDuration = TimeSpan.FromSeconds(
            builder.Configuration.GetValue("MlService:Resilience:BreakDurationSeconds", 60)),
        Name = "ml-circuit-breaker",
        ShouldHandle = new PredicateBuilder<HttpResponseMessage>()
            .Handle<HttpRequestException>()
            .Handle<TimeoutRejectedException>()
            .HandleResult(r => r.StatusCode is
                HttpStatusCode.ServiceUnavailable or
                HttpStatusCode.GatewayTimeout or
                HttpStatusCode.InternalServerError),
        OnOpened = args =>
        {
            var logger = args.Context.ServiceProvider
                .GetRequiredService<ILogger<MlClient>>();
            logger.LogError(
                "ML service circuit breaker OPENED — all calls will be rejected for {Duration} s. " +
                "Reason: {Reason}",
                args.BreakDuration.TotalSeconds,
                args.Outcome.Exception?.Message ?? args.Outcome.Result?.StatusCode.ToString());
            return ValueTask.CompletedTask;
        },
        OnClosed = args =>
        {
            var logger = args.Context.ServiceProvider
                .GetRequiredService<ILogger<MlClient>>();
            logger.LogInformation("ML service circuit breaker CLOSED — service appears healthy again.");
            return ValueTask.CompletedTask;
        },
        OnHalfOpened = args =>
        {
            var logger = args.Context.ServiceProvider
                .GetRequiredService<ILogger<MlClient>>();
            logger.LogInformation("ML service circuit breaker HALF-OPEN — sending probe request.");
            return ValueTask.CompletedTask;
        },
    });
});

// ── LLM client (tuỳ chọn — chỉ hoạt động khi Llm:ApiKey được cấu hình) ───
var llmBaseUrl = builder.Configuration["Llm:BaseUrl"];
builder.Services.AddHttpClient<LlmClient>(client =>
{
    var baseUrl = string.IsNullOrWhiteSpace(llmBaseUrl) ? "http://localhost" : llmBaseUrl;
    client.BaseAddress = new Uri(baseUrl);
    var apiKey = builder.Configuration["Llm:ApiKey"];
    if (!string.IsNullOrWhiteSpace(apiKey))
        client.DefaultRequestHeaders.Add("Authorization", $"Bearer {apiKey}");
    client.Timeout = TimeSpan.FromSeconds(120);
});

// ── Application services ──────────────────────────────────────────────────
builder.Services.AddSingleton<MarkdownChunker>();
builder.Services.AddScoped<DocumentService>();
builder.Services.AddScoped<CollectionService>();
builder.Services.AddScoped<ChatService>();

// ── Background services ───────────────────────────────────────────────────
builder.Services.AddHostedService<DocumentRetryService>();

// ── Health checks ─────────────────────────────────────────────────────────
builder.Services.AddHealthChecks()
    .AddCheck<MlServiceHealthCheck>(
        name: "ml-service",
        failureStatus: HealthStatus.Degraded,
        tags: ["ml", "external"]);

// ── SignalR ───────────────────────────────────────────────────────────────
builder.Services.AddSignalR();

// ── Controllers + CORS ───────────────────────────────────────────────────
builder.Services.AddControllers();
builder.Services.AddCors(o => o.AddDefaultPolicy(p =>
    p.WithOrigins("http://localhost:5173", "http://localhost:8000")
     .AllowAnyMethod()
     .AllowAnyHeader()
     .AllowCredentials()));

var app = builder.Build();

// ── Migrate DB khi khởi động ──────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    db.Database.Migrate();
}

app.UseCors();

// ── Static files (Vue build output) ──────────────────────────────────────
app.UseDefaultFiles();
app.UseStaticFiles();

app.MapControllers();
app.MapHub<ProgressHub>("/ws/progress");

// ── Health endpoint ───────────────────────────────────────────────────────
app.MapHealthChecks("/api/health", new Microsoft.AspNetCore.Diagnostics.HealthChecks.HealthCheckOptions
{
    ResponseWriter = HealthCheckResponseWriter.WriteJsonAsync,
});

// ── SPA fallback (Vue Router history mode) ────────────────────────────────
app.MapFallbackToFile("index.html");

app.Run();
```

---

## 5. MlClient — Wrapper Xử Lý Ngoại Lệ

Bản thân `MlClient` **không** cần thay đổi — Polly xử lý retry và circuit breaking một cách trong suốt ở tầng `HttpClient`. Tuy nhiên, các caller cần biết **loại exception** nào cần bắt khi circuit đang mở.

Khi circuit breaker mở, Polly ném ra `Polly.CircuitBreaker.BrokenCircuitException` (hoặc variant generic `BrokenCircuitException<HttpResponseMessage>`). Cần import namespace `Polly.CircuitBreaker`.

Để rõ ràng hơn, thêm một **thin helper** vào `MlClient` để tập trung việc chuyển đổi exception sang bool, chỉ được dùng bởi health check:

Phương thức `HealthAsync()` trong `MlClient` đã bắt tất cả exception và trả về `false`. Không cần thay đổi gì ở đây.

### Các caller bắt kiểu exception này:

```csharp
// Namespace Polly.CircuitBreaker
catch (BrokenCircuitException ex)
{
    // Circuit đang mở — ML service được biết là đang lỗi, fail nhanh
    logger.LogWarning("ML circuit open: {Message}", ex.Message);
    return StatusCode(503, new { status = "degraded", message = "..." });
}
```

---

## 6. Graceful Degradation Trong Controllers

### 6.1 SearchController

**File**: `OpenRAG.Api/Controllers/SearchController.cs`

```csharp
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;
using Polly.CircuitBreaker;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm, ILogger<SearchController> logger)
    : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Search(
        [FromBody] SearchRequest req,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        List<ChunkResult> results;
        try
        {
            results = await ml.SearchAsync(
                req.Query, req.Collection, req.TopK,
                req.UseReranker, req.SearchMode, ct);
        }
        catch (BrokenCircuitException ex)
        {
            logger.LogWarning("Search rejected — ML circuit open: {Message}", ex.Message);
            return StatusCode(503, new
            {
                status = "degraded",
                message = "Search is temporarily unavailable — the ML service is offline. Please try again in a minute.",
                results = Array.Empty<object>(),
                retryAfterSeconds = 60,
            });
        }
        catch (HttpRequestException ex)
        {
            logger.LogError(ex, "Search failed — ML service HTTP error");
            return StatusCode(503, new
            {
                status = "degraded",
                message = "Search failed — unable to reach the ML service. The system will retry automatically.",
                results = Array.Empty<object>(),
                retryAfterSeconds = 30,
            });
        }

        if (req.Generate && llm.IsEnabled)
        {
            try
            {
                var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
                return Ok(new SearchResponse(req.Query, results, results.Count,
                    generated?.Answer, generated?.Citations));
            }
            catch (Exception ex)
            {
                // LLM là tuỳ chọn — degrade gracefully nếu nó thất bại
                logger.LogWarning(ex, "LLM generation failed; returning raw search results");
            }
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }
}
```

### 6.2 ChatService

**File**: `OpenRAG.Api/Services/ChatService.cs`

Chat endpoint gọi `ml.SearchAsync()` bên trong. Controller cần bắt lỗi circuit breaker:

```csharp
// Trong ChatController (hoặc nơi ChatService.ChatAsync được gọi):

using Polly.CircuitBreaker;

[HttpPost]
public async Task<IActionResult> Chat([FromBody] ChatRequest req, CancellationToken ct = default)
{
    try
    {
        var response = await chatService.ChatAsync(req, ct);
        return Ok(response);
    }
    catch (BrokenCircuitException ex)
    {
        logger.LogWarning("Chat rejected — ML circuit open: {Message}", ex.Message);
        return StatusCode(503, new
        {
            status = "degraded",
            message = "Chat is temporarily unavailable — the ML service is offline. " +
                      "Your message has not been saved. Please try again in about a minute.",
            retryAfterSeconds = 60,
        });
    }
    catch (HttpRequestException ex)
    {
        logger.LogError(ex, "Chat failed — ML service HTTP error");
        return StatusCode(503, new
        {
            status = "degraded",
            message = "Chat failed due to a connection problem with the ML service.",
            retryAfterSeconds = 30,
        });
    }
}
```

### 6.3 DocumentsController — Upload

**File**: `OpenRAG.Api/Controllers/DocumentsController.cs`

```csharp
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Services;
using Polly.CircuitBreaker;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/documents")]
public class DocumentsController(DocumentService docs, ILogger<DocumentsController> logger)
    : ControllerBase
{
    private const long MaxFileSizeBytes = 100 * 1024 * 1024;
    private const int MaxTextLength = 10 * 1024 * 1024;
    private static readonly HashSet<string> AllowedExtensions =
        [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
         ".txt", ".md", ".html", ".htm", ".csv"];

    [HttpPost("upload")]
    public async Task<IActionResult> Upload(
        IFormFile file,
        [FromForm] string collection = "documents",
        CancellationToken ct = default)
    {
        if (file is null || file.Length == 0)
            return BadRequest(new { detail = "No file provided" });

        if (file.Length > MaxFileSizeBytes)
            return BadRequest(new { detail = $"File exceeds {MaxFileSizeBytes / 1024 / 1024} MB limit" });

        var ext = Path.GetExtension(file.FileName).ToLowerInvariant();
        if (!AllowedExtensions.Contains(ext))
            return BadRequest(new { detail = $"File type '{ext}' not allowed" });

        var safeFilename = Path.GetFileName(file.FileName);
        await using var stream = file.OpenReadStream();

        try
        {
            var result = await docs.IngestFileAsync(stream, safeFilename, collection, file.Length, ct);
            return Ok(result);
        }
        catch (BrokenCircuitException ex)
        {
            // Document record đã được tạo với status="failed" bên trong IngestFileAsync.
            // DocumentRetryService sẽ tự động chọn và thử lại.
            logger.LogWarning(
                "Upload for '{Filename}' queued for retry — ML circuit open: {Message}",
                safeFilename, ex.Message);
            return StatusCode(503, new
            {
                status = "queued",
                message = "The ML service is temporarily offline. Your document has been saved " +
                          "and will be indexed automatically once the service recovers. " +
                          "Check the Documents tab in a few minutes.",
                filename = safeFilename,
                retryAfterSeconds = 60,
            });
        }
        catch (HttpRequestException ex)
        {
            logger.LogError(ex, "Upload failed for '{Filename}' — ML HTTP error", safeFilename);
            return StatusCode(503, new
            {
                status = "queued",
                message = "Upload was received but indexing failed due to a connection problem. " +
                          "The system will retry automatically.",
                filename = safeFilename,
                retryAfterSeconds = 30,
            });
        }
    }

    [HttpPost("text")]
    public async Task<IActionResult> IngestText(
        [FromForm] string text,
        [FromForm] string title = "untitled",
        [FromForm] string collection = "documents",
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(text))
            return BadRequest(new { detail = "No text provided" });

        if (text.Length > MaxTextLength)
            return BadRequest(new { detail = $"Text exceeds {MaxTextLength / 1024 / 1024} MB limit" });

        try
        {
            var result = await docs.IngestTextAsync(text, title, collection, ct);
            return Ok(result);
        }
        catch (BrokenCircuitException)
        {
            return StatusCode(503, new
            {
                status = "queued",
                message = "The ML service is temporarily offline. Your text has been saved " +
                          "and will be indexed automatically once the service recovers.",
                retryAfterSeconds = 60,
            });
        }
        catch (HttpRequestException ex)
        {
            logger.LogError(ex, "Text ingest failed for '{Title}' — ML HTTP error", title);
            return StatusCode(503, new
            {
                status = "queued",
                message = "Text ingest failed due to a connection problem. The system will retry automatically.",
                retryAfterSeconds = 30,
            });
        }
    }

    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] string collection = "documents",
        CancellationToken ct = default)
    {
        var result = await docs.ListDocumentsAsync(collection, ct);
        return Ok(result);
    }

    [HttpDelete("{documentId}")]
    public async Task<IActionResult> Delete(
        Guid documentId,
        [FromQuery] string collection = "documents",
        CancellationToken ct = default)
    {
        try
        {
            var result = await docs.DeleteDocumentAsync(documentId, collection, ct);
            if (result.Status == "error")
                return NotFound(result);
            return Ok(result);
        }
        catch (BrokenCircuitException)
        {
            return StatusCode(503, new
            {
                status = "degraded",
                message = "Cannot delete document vector data — ML service is offline. " +
                          "The document record is preserved. Try again when the service recovers.",
                retryAfterSeconds = 60,
            });
        }
    }
}
```

---

## 7. Health Check Endpoint

### 7.1 Class MlServiceHealthCheck

**File**: `OpenRAG.Api/Services/HealthChecks/MlServiceHealthCheck.cs`

```csharp
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Polly.CircuitBreaker;

namespace OpenRAG.Api.Services.HealthChecks;

/// <summary>
/// Báo cáo trạng thái sống và khả năng tiếp cận của Python ML service.
/// Được đăng ký là health check có tên "ml-service" với HealthStatus.Degraded khi thất bại
/// để API tổng thể vẫn trả về 200 (thay vì 503) khi chỉ ML service bị ngắt.
/// </summary>
public class MlServiceHealthCheck(MlClient ml, ILogger<MlServiceHealthCheck> logger)
    : IHealthCheck
{
    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext context,
        CancellationToken cancellationToken = default)
    {
        try
        {
            var ok = await ml.HealthAsync(cancellationToken);
            if (ok)
                return HealthCheckResult.Healthy("ML service is reachable and healthy.");

            return HealthCheckResult.Degraded(
                "ML service returned a non-success status code.");
        }
        catch (BrokenCircuitException ex)
        {
            logger.LogWarning("ML health check: circuit is open — {Message}", ex.Message);
            return HealthCheckResult.Degraded(
                $"ML service circuit breaker is open. " +
                $"Service will be probed again after the break duration expires. " +
                $"Reason: {ex.Message}");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "ML health check threw an unexpected exception");
            return HealthCheckResult.Unhealthy(
                "ML service health check threw an exception.",
                ex);
        }
    }
}
```

### 7.2 Custom JSON response writer

**File**: `OpenRAG.Api/Services/HealthChecks/HealthCheckResponseWriter.cs`

```csharp
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using System.Text.Json;

namespace OpenRAG.Api.Services.HealthChecks;

/// <summary>
/// Ghi một báo cáo health dạng JSON có cấu trúc thay vì plain-text mặc định.
/// Định dạng response tương thích với các monitoring agent phổ biến (Prometheus, Uptime Kuma, v.v.).
/// </summary>
public static class HealthCheckResponseWriter
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public static async Task WriteJsonAsync(HttpContext ctx, HealthReport report)
    {
        ctx.Response.ContentType = "application/json";

        // Trả về 200 ngay cả khi degraded để load balancer không loại bỏ API pod.
        // Chỉ trả về 503 khi Unhealthy (API hoàn toàn thất bại, không phải chỉ ML).
        ctx.Response.StatusCode = report.Status == HealthStatus.Unhealthy ? 503 : 200;

        var body = new
        {
            status = report.Status.ToString().ToLowerInvariant(),
            totalDurationMs = (int)report.TotalDuration.TotalMilliseconds,
            checks = report.Entries.Select(e => new
            {
                name = e.Key,
                status = e.Value.Status.ToString().ToLowerInvariant(),
                description = e.Value.Description,
                durationMs = (int)e.Value.Duration.TotalMilliseconds,
                exception = e.Value.Exception?.Message,
                tags = e.Value.Tags,
            }),
        };

        await ctx.Response.WriteAsync(JsonSerializer.Serialize(body, JsonOpts));
    }
}
```

### 7.3 Ví dụ health response

```json
GET /api/health

{
  "status": "degraded",
  "totalDurationMs": 142,
  "checks": [
    {
      "name": "ml-service",
      "status": "degraded",
      "description": "ML service circuit breaker is open. Service will be probed again after the break duration expires.",
      "durationMs": 0,
      "exception": null,
      "tags": ["ml", "external"]
    }
  ]
}
```

---

## 8. Document Retry Background Service

### 8.1 Thiết kế

`DocumentRetryService` là một `IHostedService` chạy trên `PeriodicTimer`, thực hiện:

1. Tìm tất cả documents có `Status == "failed"` hoặc `Status == "indexing"` và `CreatedAt` cũ hơn 10 phút (bị kẹt trong lúc ML service khởi động lại).
2. Với mỗi document, đọc lại file gốc **nếu đã lưu**, hoặc đưa vào hàng đợi lại các raw text chunk đã có trong DB.
3. Thực hiện gọi `ml.IndexChunksAsync()` trực tiếp. Nếu circuit vẫn mở, bỏ qua và thử lại ở lần tick tiếp theo.
4. Cập nhật trạng thái document tương ứng.

**Ràng buộc thiết kế quan trọng**: `DocumentService.IngestFileAsync()` yêu cầu `Stream` gốc (byte của file). Vì các file upload không được lưu xuống đĩa theo mặc định, retry service chỉ có thể thử lại **embedding** (bước 3 của pipeline) — bước chuyển đổi markdown (bước 1) không thể phát lại nếu không có byte gốc.

Hai cách tiếp cận khả thi:

- **Option A — Lưu file gốc vào đĩa khi upload** và phát lại toàn bộ pipeline. Cần thêm cột `StoragePath` vào `Document`.
- **Option B — Chỉ retry embedding** bằng cách lưu chunked text trong DB (bảng `DocumentChunk`). Tránh phải gửi lại file gốc.
- **Option C (được triển khai bên dưới) — Minimal retry** với cột `RetryCount` + `RetryAfter`. Service cố gắng gọi lại ML endpoint `/ml/index` với dữ liệu đã chunked được lưu inline trên document, bỏ qua bước re-conversion. Cần thêm cột `ChunksJson` vào `Document`.

Option B là cách tiếp cận kiến trúc tốt nhất; Option C là nhanh nhất để triển khai. **Triển khai bên dưới dùng Option C.**

### 8.2 Mở rộng Document entity

Thêm hai cột vào `OpenRAG.Api/Models/Entities/Document.cs`:

```csharp
namespace OpenRAG.Api.Models.Entities;

public class Document
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Filename { get; set; } = "";
    public int CollectionId { get; set; }
    public Collection Collection { get; set; } = null!;
    public int ChunkCount { get; set; }
    public long SizeBytes { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? IndexedAt { get; set; }
    public string Status { get; set; } = "indexing"; // indexing | indexed | failed | retry_pending

    // ── Hỗ trợ Retry ─────────────────────────────────────────────────────
    /// <summary>
    /// JSON-serialised List&lt;MlChunkInput&gt; được lưu sau khi chunking thành công.
    /// Cho phép retry service thử lại embedding mà không cần re-convert file.
    /// Null khi document thất bại trước hoặc trong quá trình chunking.
    /// </summary>
    public string? ChunksJson { get; set; }

    /// <summary>Tên collection được cache ở đây để retry job không cần JOIN.</summary>
    public string? CollectionName { get; set; }

    /// <summary>Số lần đã thử lại tự động.</summary>
    public int RetryCount { get; set; } = 0;

    /// <summary>Thời điểm thực tế sau đó retry tiếp theo mới được phép thực hiện.</summary>
    public DateTime? RetryAfter { get; set; }
}
```

Thêm một EF Core migration mới sau khi thay đổi entity:

```bash
dotnet ef migrations add AddDocumentRetryColumns -p OpenRAG.Api
```

### 8.3 Thay đổi DocumentService — lưu chunks sau khi chunking

Trong `DocumentService.IngestFileAsync()` và `IngestTextAsync()`, lưu các chunk vào `doc.ChunksJson` ngay sau khi chunking, trước lần gọi ML. Điều này đảm bảo dữ liệu retry luôn có sẵn ngay cả khi lần gọi ML thất bại:

```csharp
// Sau: var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
// THÊM:
doc.ChunksJson = System.Text.Json.JsonSerializer.Serialize(mlChunks);
doc.CollectionName = collection;
await db.SaveChangesAsync(ct);  // lưu trước lần gọi ML có thể thất bại
```

### 8.4 DocumentRetryService

**File**: `OpenRAG.Api/Services/Background/DocumentRetryService.cs`

```csharp
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Services;
using Polly.CircuitBreaker;
using System.Text.Json;

namespace OpenRAG.Api.Services.Background;

/// <summary>
/// Background service định kỳ quét các documents ở trạng thái "failed" hoặc "indexing" bị kẹt
/// và thử lại embedding qua ML service.
///
/// Lịch: mỗi 5 phút (có thể cấu hình qua MlService:Retry:IntervalMinutes).
/// Một document được thử lại tối đa MlService:Retry:MaxAttempts lần (mặc định: 5).
/// Sau số lần tối đa, document được giữ ở trạng thái "failed" vĩnh viễn.
/// </summary>
public sealed class DocumentRetryService(
    IServiceScopeFactory scopeFactory,
    IConfiguration config,
    ILogger<DocumentRetryService> logger)
    : BackgroundService
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var intervalMinutes = config.GetValue("MlService:Retry:IntervalMinutes", 5);
        var maxAttempts = config.GetValue("MlService:Retry:MaxAttempts", 5);
        var stuckMinutes = config.GetValue("MlService:Retry:StuckAfterMinutes", 10);

        logger.LogInformation(
            "DocumentRetryService started — checking every {Interval} min, max {Max} attempts.",
            intervalMinutes, maxAttempts);

        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(intervalMinutes));

        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            await ProcessRetryBatchAsync(maxAttempts, stuckMinutes, stoppingToken);
        }
    }

    private async Task ProcessRetryBatchAsync(
        int maxAttempts, int stuckMinutes, CancellationToken ct)
    {
        await using var scope = scopeFactory.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var ml = scope.ServiceProvider.GetRequiredService<MlClient>();

        var stuckCutoff = DateTime.UtcNow.AddMinutes(-stuckMinutes);
        var now = DateTime.UtcNow;

        // Tìm các candidates:
        //  a) Status == "failed" và có ChunksJson (có thể retry embedding)
        //  b) Status == "indexing" cũ hơn stuckMinutes (có thể bị kill giữa chừng)
        //  Cả hai: retry count dưới giới hạn, và RetryAfter đã qua (hoặc null)
        var candidates = await db.Documents
            .Where(d =>
                (d.Status == "failed" || (d.Status == "indexing" && d.CreatedAt < stuckCutoff))
                && d.ChunksJson != null
                && d.RetryCount < maxAttempts
                && (d.RetryAfter == null || d.RetryAfter <= now))
            .OrderBy(d => d.RetryCount)
            .ThenBy(d => d.CreatedAt)
            .Take(10)   // xử lý theo batch nhỏ để không làm quá tải ML service
            .ToListAsync(ct);

        if (candidates.Count == 0)
        {
            logger.LogDebug("DocumentRetryService: no documents to retry.");
            return;
        }

        logger.LogInformation(
            "DocumentRetryService: found {Count} document(s) to retry.", candidates.Count);

        foreach (var doc in candidates)
        {
            if (ct.IsCancellationRequested) break;

            doc.RetryCount++;
            // Exponential back-off cho lần retry tiếp theo: 5 phút, 10 phút, 20 phút, 40 phút, 80 phút
            doc.RetryAfter = now.AddMinutes(5 * Math.Pow(2, doc.RetryCount - 1));

            try
            {
                var chunks = JsonSerializer.Deserialize<List<MlChunkInput>>(
                    doc.ChunksJson!, JsonOpts)
                    ?? throw new InvalidOperationException("Deserialized null chunks");

                var collectionName = doc.CollectionName ?? "documents";

                logger.LogInformation(
                    "Retrying document {Id} ('{Filename}'), attempt {Attempt}/{Max}",
                    doc.Id, doc.Filename, doc.RetryCount, maxAttempts);

                // Đảm bảo vector collection tồn tại (idempotent)
                await ml.EnsureCollectionAsync(collectionName, ct);

                var result = await ml.IndexChunksAsync(
                    new MlIndexRequest(doc.Id.ToString(), collectionName, chunks), ct);

                doc.Status = "indexed";
                doc.ChunkCount = result.ChunkCount;
                doc.IndexedAt = DateTime.UtcNow;
                doc.ChunksJson = null;  // giải phóng storage sau khi index thành công

                logger.LogInformation(
                    "Document {Id} ('{Filename}') successfully indexed on retry — {Chunks} chunks.",
                    doc.Id, doc.Filename, result.ChunkCount);
            }
            catch (BrokenCircuitException ex)
            {
                // Circuit đang mở — không tiêu tốn retry counter, chỉ chờ
                doc.RetryCount--;
                doc.RetryAfter = now.AddSeconds(61); // chờ hơi quá break duration một chút
                logger.LogWarning(
                    "Document retry skipped for {Id} — ML circuit open: {Message}", doc.Id, ex.Message);
            }
            catch (Exception ex)
            {
                // Đánh dấu là failed; retry counter đã được tăng trước đó
                doc.Status = doc.RetryCount >= maxAttempts ? "failed" : "failed";
                logger.LogError(ex,
                    "Document retry failed for {Id} ('{Filename}'), attempt {Attempt}",
                    doc.Id, doc.Filename, doc.RetryCount);

                if (doc.RetryCount >= maxAttempts)
                {
                    logger.LogError(
                        "Document {Id} ('{Filename}') has exhausted all {Max} retry attempts. " +
                        "Manual intervention required.",
                        doc.Id, doc.Filename, maxAttempts);
                }
            }

            await db.SaveChangesAsync(ct);
        }
    }
}
```

---

## 9. Frontend UX (Vue)

### 9.1 Shared API error utility

**File**: `frontend/src/api/errors.ts`  (file mới)

```typescript
import type { AxiosError } from 'axios'

export interface DegradedResponse {
  status: 'degraded' | 'queued'
  message: string
  retryAfterSeconds?: number
  results?: unknown[]
}

/**
 * Trả về true khi server phản hồi với 503 và body degraded có cấu trúc.
 */
export function isDegradedResponse(err: AxiosError): boolean {
  return err.response?.status === 503
}

/**
 * Trích xuất thông báo hiển thị cho người dùng từ một 503 degraded response,
 * hoặc fallback về thông báo chung chung.
 */
export function getDegradedMessage(err: AxiosError): string {
  const data = err.response?.data as DegradedResponse | undefined
  if (data?.message) return data.message
  if (err.response?.status === 503)
    return 'The service is temporarily unavailable. Please try again shortly.'
  return err.message ?? 'An unexpected error occurred.'
}

/**
 * Trả về số giây retry-after từ một 503 response, hoặc undefined.
 */
export function getRetryAfter(err: AxiosError): number | undefined {
  const data = err.response?.data as DegradedResponse | undefined
  return data?.retryAfterSeconds
}
```

### 9.2 SearchTab.vue — cải thiện xử lý lỗi

**File**: `frontend/src/components/SearchTab.vue`

Thay đổi hàm `doSearch` và template hiển thị lỗi:

```typescript
// Thay thế phần doSearch() và error ref hiện tại:

import { getDegradedMessage, isDegradedResponse } from '../api/errors'
import type { AxiosError } from 'axios'

const error = ref('')
const isDegraded = ref(false)
const retryAfter = ref<number | undefined>(undefined)

async function doSearch() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  isDegraded.value = false
  retryAfter.value = undefined
  answer.value = null
  citations.value = []

  try {
    const { data } = await search(query.value, collection.value, topK.value, {
      useReranker: useReranker.value,
      searchMode: searchMode.value,
      generate: generate.value,
    })
    results.value = data.results
    answer.value = data.answer ?? null
    citations.value = data.citations ?? []
  } catch (e: unknown) {
    const axiosErr = e as AxiosError
    isDegraded.value = isDegradedResponse(axiosErr)
    error.value = getDegradedMessage(axiosErr)
    retryAfter.value = isDegraded.value
      ? (axiosErr.response?.data as any)?.retryAfterSeconds
      : undefined
    results.value = []
  } finally {
    loading.value = false
  }
}
```

Cập nhật template block lỗi (thay thế `<div v-if="error" ...>` hiện có):

```html
<!-- Trạng thái lỗi / Degraded -->
<div
  v-if="error"
  :class="[
    'border rounded-lg px-4 py-3 text-sm flex items-start gap-3',
    isDegraded
      ? 'bg-amber-900/30 border-amber-700 text-amber-300'
      : 'bg-red-900/30 border-red-700 text-red-400'
  ]"
>
  <!-- Icon -->
  <svg v-if="isDegraded" class="w-5 h-5 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
      d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
  </svg>
  <svg v-else class="w-5 h-5 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
      d="M6 18L18 6M6 6l12 12" />
  </svg>

  <div class="flex-1">
    <p class="font-medium">{{ isDegraded ? 'Service Degraded' : 'Search Error' }}</p>
    <p class="mt-0.5 opacity-90">{{ error }}</p>
    <p v-if="retryAfter" class="mt-1 text-xs opacity-70">
      Automatic retry available in ~{{ retryAfter }} seconds.
    </p>
  </div>

  <button
    v-if="isDegraded"
    @click="doSearch"
    class="shrink-0 text-xs px-2 py-1 rounded bg-amber-700/50 hover:bg-amber-600/50 transition-colors"
  >
    Retry
  </button>
</div>
```

### 9.3 UploadTab.vue — cải thiện xử lý lỗi

**File**: `frontend/src/components/UploadTab.vue`

```typescript
// Thay thế catch block trong uploadAll:

import { getDegradedMessage, isDegradedResponse, getRetryAfter } from '../api/errors'
import type { AxiosError } from 'axios'

// Mở rộng FileEntry để theo dõi trạng thái degraded:
interface FileEntry {
  file: File
  status: 'pending' | 'uploading' | 'done' | 'error' | 'queued'
  message: string
  documentId?: string
  retryAfter?: number
}

// Trong uploadAll():
async function uploadAll() {
  for (const item of files.value.filter(f => f.status === 'pending')) {
    item.status = 'uploading'
    try {
      const { data } = await uploadFile(item.file, collection.value)
      item.documentId = data.documentId
      item.status = 'done'
      item.message = data.message
      store.fetch()
    } catch (e: unknown) {
      const axiosErr = e as AxiosError
      if (isDegradedResponse(axiosErr)) {
        // 503 với body "queued" hoặc "degraded"
        const body = axiosErr.response?.data as any
        item.status = 'queued'
        item.message = body?.message ?? 'Queued for retry — ML service offline.'
        item.retryAfter = getRetryAfter(axiosErr)
      } else {
        item.status = 'error'
        item.message = getDegradedMessage(axiosErr)
      }
    }
  }
}

// Cập nhật statusClass để bao gồm 'queued':
const statusClass = (s: string) => ({
  pending: 'text-slate-400',
  uploading: 'text-yellow-400 animate-pulse',
  done: 'text-green-400',
  queued: 'text-amber-400',
  error: 'text-red-400',
}[s] ?? 'text-slate-400')
```

Cập nhật template danh sách file để hiển thị trạng thái queued:

```html
<!-- Bên trong vòng lặp v-for file, thay thế phần hiển thị status: -->
<span :class="['text-xs ml-2 max-w-xs truncate', statusClass(f.status)]">
  <template v-if="f.status === 'uploading' && getProgress(f)">
    {{ stageLabel(getProgress(f)!.stage) }}
  </template>
  <template v-else-if="f.status === 'queued'">
    Queued for retry — will index automatically
  </template>
  <template v-else-if="f.status === 'done' || f.status === 'error'">
    {{ f.message }}
  </template>
  <template v-else>{{ f.status }}</template>
</span>
```

---

## 10. Cấu Hình (appsettings.json)

Thêm block cấu hình resilience đầy đủ vào `OpenRAG.Api/appsettings.json`:

```json
{
  "ConnectionStrings": {
    "Default": "Data Source=../data/openrag.db"
  },
  "MlService": {
    "BaseUrl": "http://localhost:8001",
    "Resilience": {
      "AttemptTimeoutSeconds": 30,
      "MaxRetryAttempts": 3,
      "SamplingWindowSeconds": 30,
      "FailureRatio": 0.5,
      "MinimumThroughput": 5,
      "BreakDurationSeconds": 60
    },
    "Retry": {
      "IntervalMinutes": 5,
      "MaxAttempts": 5,
      "StuckAfterMinutes": 10
    }
  },
  "Llm": {
    "BaseUrl": "",
    "ApiKey": "",
    "Model": "gpt-4o-mini"
  },
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Microsoft.AspNetCore": "Warning",
      "Polly": "Warning"
    }
  },
  "AllowedHosts": "*",
  "Urls": "http://0.0.0.0:8000"
}
```

**Override cho môi trường Production** (`appsettings.Production.json`):

```json
{
  "MlService": {
    "Resilience": {
      "AttemptTimeoutSeconds": 60,
      "MaxRetryAttempts": 2,
      "BreakDurationSeconds": 120,
      "MinimumThroughput": 10
    },
    "Retry": {
      "IntervalMinutes": 10,
      "MaxAttempts": 3
    }
  }
}
```

---

## 11. Lưu Ý Khi Triển Khai

### 11.1 Tính toán Timeout

Với pipeline được cấu hình như trên, **độ trễ tệ nhất** trước khi caller nhận được 503 là:

```
attempts × per-attempt-timeout + sum(backoff delays)
= 4 × 30 s + (1 + 2 + 4) s
= 127 s
```

Đây là sự thay thế cho blanket timeout 300 giây hiện tại. Đặt `HttpClient.Timeout = 150 s` (như đã trình bày trong `Program.cs`) làm giới hạn an toàn phía trên.

Đối với document embedding (file lớn), per-attempt timeout có thể cần là 60 giây trong môi trường production. Dùng `appsettings.Production.json` để override.

### 11.2 Bypass HealthAsync

Phương thức `MlClient.HealthAsync()` được gọi bởi health check trên một `PeriodicTimer` ngắn. Nó **không được** đi qua pipeline retry/circuit-breaker (nếu không, một health check đơn lẻ có thể kích hoạt circuit mở).

Hai lựa chọn:
- **Option A**: Đăng ký một `HttpClient` riêng có tên `"ml-health"` không có Polly, chỉ dùng cho health check.
- **Option B**: Health check đã bắt `BrokenCircuitException` và trả về `Degraded` trước khi bất kỳ lần gọi HTTP nào được thực hiện — Polly ném ngay lập tức khi circuit mở, vì vậy health check nhanh chóng.

Option B hoạt động tốt ngay từ đầu vì circuit breaker là chiến lược ngoài cùng: khi circuit mở, nó ném `BrokenCircuitException` trước khi các chiến lược timeout hoặc retry kích hoạt. Try/catch trong `HealthAsync()` của `MlClient` cũng sẽ bắt exception này và trả về `false`.

### 11.3 SignalR progress trong quá trình retry

Khi `DocumentRetryService` re-index một document, nó hiện tại **không** phát sự kiện SignalR progress vì không có quyền truy cập vào `IHubContext<ProgressHub>`. Để thêm progress:

- Inject `IHubContext<ProgressHub>` vào `DocumentRetryService`.
- Phát sự kiện stage `"retry_indexing"` khi bắt đầu retry và `"done"` / `"failed"` khi hoàn thành.
- Ở phía frontend, lắng nghe các sự kiện này trong `UploadTab.vue` và cập nhật documents có `status === "queued"`.

### 11.4 EF Core migration

Sau khi thêm các cột `ChunksJson`, `CollectionName`, `RetryCount`, và `RetryAfter` vào `Document`:

```bash
cd OpenRAG.Api
dotnet ef migrations add AddDocumentRetryColumns
dotnet ef database update
```

Migration sẽ là một `ALTER TABLE` chỉ thêm thuần tuý — không mất dữ liệu.

### 11.5 Chi phí lưu trữ ChunksJson

`ChunksJson` lưu toàn bộ text của tất cả chunks cho một document. Với PDF 100 MB, có thể là vài MB JSON. Các phương án giảm thiểu:

- Đặt `doc.ChunksJson = null` ngay sau khi indexing thành công (đã thực hiện trong retry service).
- Thêm kiểm tra kích thước: bỏ qua việc lưu `ChunksJson` nếu độ dài serialised vượt quá, ví dụ, 10 MB (các document rất lớn có thể đơn giản được upload lại nếu ML bị ngắt).
- Chuyển sang blob/file storage trong production (S3, Azure Blob).

### 11.6 Lưu ý về Kubernetes / Docker

- **Readiness probe**: trỏ đến `/api/health`. Khi ML bị degraded, trả về 200 (để API pod vẫn trong rotation — nó vẫn có thể phục vụ cached data và queue uploads). Chỉ trả về 503 khi bản thân API (DB) bị unhealthy.
- **Liveness probe**: một `GET /api/health` đơn giản với interval 30 giây; dùng `failureThreshold: 3` trước khi kill pod.
- **ML service readiness**: cấu hình Kubernetes chỉ route traffic đến ML pod khi `/ml/health` trả về 200. Điều này ngăn circuit bị mở trong lúc pod khởi động bình thường.

### 11.7 Observability

Cả ba chiến lược Polly đều phát ra các sự kiện có tên (`OnRetry`, `OnOpened`, `OnClosed`, `OnHalfOpened`) ghi các log có cấu trúc thông qua `ILogger`. Chúng tương thích với bất kỳ log sink nào (Serilog, Application Insights, v.v.).

Để thêm metrics:

```csharp
// Trong Program.cs, sau AddResilienceHandler:
builder.Services.AddOpenTelemetry()
    .WithMetrics(m => m.AddAspNetCoreInstrumentation()
                       .AddHttpClientInstrumentation());
```

Polly v8 tự động phát ra OpenTelemetry metrics cho retry count, thay đổi trạng thái circuit, và timeout count dưới namespace meter `polly.*`.

---

## Tổng Kết Các File Mới

| File | Mục đích |
|---|---|
| `OpenRAG.Api/Services/HealthChecks/MlServiceHealthCheck.cs` | `IHealthCheck` ping đến `/ml/health` |
| `OpenRAG.Api/Services/HealthChecks/HealthCheckResponseWriter.cs` | JSON formatter cho `/api/health` |
| `OpenRAG.Api/Services/Background/DocumentRetryService.cs` | `BackgroundService` thử lại các document thất bại |
| `frontend/src/api/errors.ts` | Shared utilities phân tích 503 cho Vue |

## Tổng Kết Các File Được Chỉnh Sửa

| File | Thay đổi |
|---|---|
| `OpenRAG.Api/Program.cs` | Đăng ký Polly pipeline, health checks, hosted service |
| `OpenRAG.Api/Controllers/SearchController.cs` | Bắt `BrokenCircuitException`, trả về 503 |
| `OpenRAG.Api/Controllers/DocumentsController.cs` | Bắt `BrokenCircuitException`, trả về 503 với gợi ý retry |
| `OpenRAG.Api/Controllers/ChatController.cs` | Bắt `BrokenCircuitException`, trả về 503 |
| `OpenRAG.Api/Models/Entities/Document.cs` | Thêm `ChunksJson`, `CollectionName`, `RetryCount`, `RetryAfter` |
| `OpenRAG.Api/Services/DocumentService.cs` | Lưu `ChunksJson` + `CollectionName` trước lần gọi ML |
| `frontend/src/components/SearchTab.vue` | Hiển thị trạng thái degraded + nút retry |
| `frontend/src/components/UploadTab.vue` | Hiển thị trạng thái queued cho upload 503 |
