# ML Service Resilience — Circuit Breaker Pattern Design

**Status**: Design proposal
**Target**: OpenRAG.Api (.NET 8)
**Date**: 2026-03-26

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Solution Overview](#2-solution-overview)
3. [NuGet Packages](#3-nuget-packages)
4. [Polly Circuit Breaker — Program.cs Setup](#4-polly-circuit-breaker--programcs-setup)
5. [MlClient — Exception Handling Wrapper](#5-mlclient--exception-handling-wrapper)
6. [Graceful Degradation in Controllers](#6-graceful-degradation-in-controllers)
7. [Health Check Endpoint](#7-health-check-endpoint)
8. [Document Retry Background Service](#8-document-retry-background-service)
9. [Frontend UX (Vue)](#9-frontend-ux-vue)
10. [Configuration (appsettings.json)](#10-configuration-appsettingsjson)
11. [Deployment Considerations](#11-deployment-considerations)

---

## 1. Problem Statement

The Python ML service (`http://localhost:8001`) is a hard dependency for all major OpenRAG operations. When it is unavailable, the following cascading failures occur:

| Scenario | Current Behaviour | Impact |
|---|---|---|
| ML service is down | `HttpRequestException` propagates uncaught | HTTP 500 with no user-readable message |
| ML service is slow / overloaded | Every request holds a connection for up to **300 s** | Thread pool exhaustion under load |
| ML service restarts | All in-flight requests fail immediately | User loses work, re-upload required |
| Repeated ML failures | No circuit break — every call still attempts a live connection | Thundering herd, service can't recover |
| Upload fails mid-index | `Document.Status = "failed"` with no retry mechanism | Documents stuck permanently in failed/indexing state |

### Root cause locations

- `OpenRAG.Api/Program.cs` line 15–19: `HttpClient.Timeout = 300 s`, no resilience pipeline.
- `OpenRAG.Api/Services/MlClient.cs`: every method calls `EnsureSuccessStatusCode()` without catching `HttpRequestException` or `BrokenCircuitException`.
- `OpenRAG.Api/Controllers/SearchController.cs` and `ChatService.cs`: no try/catch around `ml.SearchAsync()`.
- `OpenRAG.Api/Controllers/DocumentsController.cs`: returns the exception's 500 to the client.
- No `BackgroundService` to retry documents stuck in `"failed"` or `"indexing"` status.

---

## 2. Solution Overview

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

The project already has `Microsoft.Extensions.Http.Resilience` 10.4.0 in `OpenRAG.Api.csproj`. That package provides the full Polly v8 integration via `AddStandardResilienceHandler()` and `AddResilienceHandler()`. No additional packages are required.

```xml
<!-- OpenRAG.Api/OpenRAG.Api.csproj — already present, no change needed -->
<PackageReference Include="Microsoft.Extensions.Http.Resilience" Version="10.4.0" />

<!-- Health checks UI (optional, for /health endpoint with rich JSON) -->
<PackageReference Include="Microsoft.Extensions.Diagnostics.HealthChecks" Version="8.0.*" />
```

`Microsoft.Extensions.Diagnostics.HealthChecks` ships with the .NET 8 SDK meta-package, so no separate install is needed for the health check extension methods (`AddHealthChecks()`, `MapHealthChecks()`).

---

## 4. Polly Circuit Breaker — Program.cs Setup

### 4.1 Strategy

Use `AddResilienceHandler()` (from `Microsoft.Extensions.Http.Resilience`) to attach a named Polly pipeline to the `MlClient` `HttpClient`. This is preferred over `AddStandardResilienceHandler()` because the standard handler's defaults (especially its aggressive circuit-breaker thresholds) may conflict with the ML service's expected long-running embedding calls.

Three strategies are nested in order (outer → inner):

1. **Timeout** — 30 s per individual attempt (catches runaway calls before retry).
2. **Retry** — 3 additional attempts (4 total) with exponential backoff: 1 s → 2 s → 4 s. Only retries transient HTTP errors (5xx, 408, network failures).
3. **Circuit Breaker** — sampling-based: opens after ≥ 5 failures in a 30 s window (failure ratio ≥ 50 %). Stays open for 60 s; then half-opens to let one probe through.

### 4.2 Complete `Program.cs`

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

// ── ML Service HTTP client with Polly resilience pipeline ─────────────────
builder.Services.AddHttpClient<MlClient>(client =>
{
    client.BaseAddress = new Uri(
        builder.Configuration["MlService:BaseUrl"] ?? "http://localhost:8001");

    // Remove the blanket 300 s timeout — per-attempt timeout is now managed by
    // the Polly TimeoutStrategy below. Set the HttpClient timeout slightly above
    // the maximum total time (per-attempt 30 s × 4 attempts + 3 backoff delays
    // up to 7 s = ~127 s). Use 150 s as a safety ceiling.
    client.Timeout = TimeSpan.FromSeconds(150);
})
.AddResilienceHandler("ml-resilience", pipeline =>
{
    // ── 1. Per-attempt timeout (innermost — evaluated first on each try) ──
    pipeline.AddTimeout(new HttpTimeoutStrategyOptions
    {
        Timeout = TimeSpan.FromSeconds(
            builder.Configuration.GetValue("MlService:Resilience:AttemptTimeoutSeconds", 30)),
        Name = "ml-attempt-timeout",
    });

    // ── 2. Retry with exponential back-off ────────────────────────────────
    pipeline.AddRetry(new HttpRetryStrategyOptions
    {
        MaxRetryAttempts = builder.Configuration.GetValue("MlService:Resilience:MaxRetryAttempts", 3),
        BackoffType = DelayBackoffType.Exponential,
        Delay = TimeSpan.FromSeconds(1),   // 1 s, 2 s, 4 s
        UseJitter = true,                   // avoids thundering-herd on simultaneous retries
        Name = "ml-retry",
        // Retry on transient HTTP failures and timeouts but NOT on 4xx client errors
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

    // ── 3. Circuit breaker (outermost — trips after repeated failures) ────
    pipeline.AddCircuitBreaker(new HttpCircuitBreakerStrategyOptions
    {
        // Sampling window: track outcomes over 30 s
        SamplingDuration = TimeSpan.FromSeconds(
            builder.Configuration.GetValue("MlService:Resilience:SamplingWindowSeconds", 30)),
        // Open circuit if ≥ 50 % of calls fail AND at least 5 have been made
        FailureRatio = builder.Configuration.GetValue("MlService:Resilience:FailureRatio", 0.5),
        MinimumThroughput = builder.Configuration.GetValue("MlService:Resilience:MinimumThroughput", 5),
        // Stay open (reject immediately) for 60 s
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

// ── LLM client (optional — only active when Llm:ApiKey is configured) ─────
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

// ── Migrate DB on startup ─────────────────────────────────────────────────
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

## 5. MlClient — Exception Handling Wrapper

The `MlClient` itself does **not** need to change — Polly handles retries and circuit breaking transparently at the `HttpClient` level. However, callers need to know **which exception type** to catch when the circuit is open.

When the circuit breaker is open, Polly throws `Polly.CircuitBreaker.BrokenCircuitException` (or its generic variant `BrokenCircuitException<HttpResponseMessage>`). Import namespace `Polly.CircuitBreaker`.

For clarity, add a **thin helper** to `MlClient` that centralises the exception-to-bool translation, used only by the health check:

The `HealthAsync()` method in `MlClient` already catches all exceptions and returns `false`. No changes needed there.

### Callers catch this exception type:

```csharp
// Polly.CircuitBreaker namespace
catch (BrokenCircuitException ex)
{
    // Circuit is open — ML service is known-bad, fail fast
    logger.LogWarning("ML circuit open: {Message}", ex.Message);
    return StatusCode(503, new { status = "degraded", message = "..." });
}
```

---

## 6. Graceful Degradation in Controllers

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
                // LLM is optional — degrade gracefully if it fails
                logger.LogWarning(ex, "LLM generation failed; returning raw search results");
            }
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }
}
```

### 6.2 ChatService

**File**: `OpenRAG.Api/Services/ChatService.cs`

The chat endpoint calls `ml.SearchAsync()` internally. The controller needs to catch circuit breaker errors:

```csharp
// In ChatController (or wherever ChatService.ChatAsync is called):

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
            // Document record has already been created with status="failed" inside IngestFileAsync.
            // The DocumentRetryService will pick it up and retry automatically.
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

### 7.1 MlServiceHealthCheck class

**File**: `OpenRAG.Api/Services/HealthChecks/MlServiceHealthCheck.cs`

```csharp
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Polly.CircuitBreaker;

namespace OpenRAG.Api.Services.HealthChecks;

/// <summary>
/// Reports the liveness and reachability of the Python ML service.
/// Registered as a named health check "ml-service" with HealthStatus.Degraded on failure
/// so the overall API still returns 200 (rather than 503) when only the ML service is down.
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
/// Writes a structured JSON health report instead of the plain-text default.
/// Response format is compatible with common monitoring agents (Prometheus, Uptime Kuma, etc.).
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

        // Return 200 even when degraded so load balancers don't yank the API pod.
        // Only return 503 when Unhealthy (complete API failure, not just ML).
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

### 7.3 Sample health response

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

### 8.1 Design

`DocumentRetryService` is an `IHostedService` running on a `PeriodicTimer` that:

1. Finds all documents with `Status == "failed"` or `Status == "indexing"` and `CreatedAt` older than 10 minutes (stuck during an ML restart).
2. For each, re-reads the original file **if stored**, or re-queues the raw text chunks already in the DB.
3. Attempts to call `ml.IndexChunksAsync()` directly. If the circuit is still open, it skips and will retry on the next tick.
4. Updates the document status accordingly.

**Important design constraint**: `DocumentService.IngestFileAsync()` requires the original `Stream` (file bytes). Since uploaded files are not persisted to disk by default, the retry service can only re-attempt **embedding** (step 3 of the pipeline) — the markdown conversion step (step 1) cannot be replayed without the original bytes.

Two approaches are feasible:

- **Option A — Store original file to disk on upload** and replay the full pipeline. Requires adding a `StoragePath` column to `Document`.
- **Option B — Retry embedding only** by storing chunked text in the DB (a `DocumentChunk` table). This avoids re-sending the original file.
- **Option C (implemented below) — Minimal retry** with a `RetryCount` + `RetryAfter` column. The service tries to re-call the ML `/ml/index` endpoint with the already-chunked data stored inline on the document, skipping re-conversion. This requires adding a `ChunksJson` column to `Document`.

Option B is the most architecturally sound; Option C is the fastest to implement. **The implementation below uses Option C.**

### 8.2 Document entity extension

Add two columns to `OpenRAG.Api/Models/Entities/Document.cs`:

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

    // ── Retry support ────────────────────────────────────────────────────
    /// <summary>
    /// JSON-serialised List&lt;MlChunkInput&gt; stored after chunking succeeds.
    /// Allows the retry service to re-attempt embedding without re-converting the file.
    /// Null when the document failed before or during chunking.
    /// </summary>
    public string? ChunksJson { get; set; }

    /// <summary>Collection name cached here so the retry job doesn't need a JOIN.</summary>
    public string? CollectionName { get; set; }

    /// <summary>How many automatic retry attempts have been made.</summary>
    public int RetryCount { get; set; } = 0;

    /// <summary>Wall-clock time after which the next retry is allowed.</summary>
    public DateTime? RetryAfter { get; set; }
}
```

Add a new EF Core migration after changing the entity:

```bash
dotnet ef migrations add AddDocumentRetryColumns -p OpenRAG.Api
```

### 8.3 DocumentService changes — save chunks after chunking

In `DocumentService.IngestFileAsync()` and `IngestTextAsync()`, persist the chunks to `doc.ChunksJson` immediately after chunking, before the ML call. This ensures retry data is available even if the ML call fails:

```csharp
// After: var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
// ADD:
doc.ChunksJson = System.Text.Json.JsonSerializer.Serialize(mlChunks);
doc.CollectionName = collection;
await db.SaveChangesAsync(ct);  // persist before the ML call that might fail
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
/// Background service that periodically scans for documents in "failed" or stuck "indexing"
/// status and re-attempts embedding via the ML service.
///
/// Schedule: every 5 minutes (configurable via MlService:Retry:IntervalMinutes).
/// A document is retried up to MlService:Retry:MaxAttempts times (default: 5).
/// After the maximum attempts the document is left in "failed" status permanently.
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

        // Find candidates:
        //  a) Status == "failed" with ChunksJson available (can retry embedding)
        //  b) Status == "indexing" older than stuckMinutes (likely killed mid-way)
        //  Both: retry count below limit, and RetryAfter has passed (or is null)
        var candidates = await db.Documents
            .Where(d =>
                (d.Status == "failed" || (d.Status == "indexing" && d.CreatedAt < stuckCutoff))
                && d.ChunksJson != null
                && d.RetryCount < maxAttempts
                && (d.RetryAfter == null || d.RetryAfter <= now))
            .OrderBy(d => d.RetryCount)
            .ThenBy(d => d.CreatedAt)
            .Take(10)   // process in small batches to avoid overwhelming the ML service
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
            // Exponential back-off for next retry: 5 min, 10 min, 20 min, 40 min, 80 min
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

                // Ensure the vector collection exists (idempotent)
                await ml.EnsureCollectionAsync(collectionName, ct);

                var result = await ml.IndexChunksAsync(
                    new MlIndexRequest(doc.Id.ToString(), collectionName, chunks), ct);

                doc.Status = "indexed";
                doc.ChunkCount = result.ChunkCount;
                doc.IndexedAt = DateTime.UtcNow;
                doc.ChunksJson = null;  // free storage once successfully indexed

                logger.LogInformation(
                    "Document {Id} ('{Filename}') successfully indexed on retry — {Chunks} chunks.",
                    doc.Id, doc.Filename, result.ChunkCount);
            }
            catch (BrokenCircuitException ex)
            {
                // Circuit is open — don't burn the retry counter, just wait
                doc.RetryCount--;
                doc.RetryAfter = now.AddSeconds(61); // wait slightly over break duration
                logger.LogWarning(
                    "Document retry skipped for {Id} — ML circuit open: {Message}", doc.Id, ex.Message);
            }
            catch (Exception ex)
            {
                // Mark as failed; retry counter has already been incremented
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

**File**: `frontend/src/api/errors.ts`  (new file)

```typescript
import type { AxiosError } from 'axios'

export interface DegradedResponse {
  status: 'degraded' | 'queued'
  message: string
  retryAfterSeconds?: number
  results?: unknown[]
}

/**
 * Returns true when the server responded with 503 and a structured degraded body.
 */
export function isDegradedResponse(err: AxiosError): boolean {
  return err.response?.status === 503
}

/**
 * Extracts the user-facing message from a 503 degraded response,
 * or falls back to a generic message.
 */
export function getDegradedMessage(err: AxiosError): string {
  const data = err.response?.data as DegradedResponse | undefined
  if (data?.message) return data.message
  if (err.response?.status === 503)
    return 'The service is temporarily unavailable. Please try again shortly.'
  return err.message ?? 'An unexpected error occurred.'
}

/**
 * Returns retry-after seconds from a 503 response, or undefined.
 */
export function getRetryAfter(err: AxiosError): number | undefined {
  const data = err.response?.data as DegradedResponse | undefined
  return data?.retryAfterSeconds
}
```

### 9.2 SearchTab.vue — improved error handling

**File**: `frontend/src/components/SearchTab.vue`

Change the `doSearch` function and the error display template:

```typescript
// Replace the existing doSearch() and error ref section:

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

Update the error template block (replace the existing `<div v-if="error" ...>`):

```html
<!-- Error / Degraded state -->
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

### 9.3 UploadTab.vue — improved error handling

**File**: `frontend/src/components/UploadTab.vue`

```typescript
// Replace the uploadAll catch block:

import { getDegradedMessage, isDegradedResponse, getRetryAfter } from '../api/errors'
import type { AxiosError } from 'axios'

// Extend FileEntry to track degraded state:
interface FileEntry {
  file: File
  status: 'pending' | 'uploading' | 'done' | 'error' | 'queued'
  message: string
  documentId?: string
  retryAfter?: number
}

// In uploadAll():
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
        // 503 with "queued" or "degraded" body
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

// Update statusClass to include 'queued':
const statusClass = (s: string) => ({
  pending: 'text-slate-400',
  uploading: 'text-yellow-400 animate-pulse',
  done: 'text-green-400',
  queued: 'text-amber-400',
  error: 'text-red-400',
}[s] ?? 'text-slate-400')
```

Update the file list template to show queued state:

```html
<!-- Inside the v-for file loop, replace the status display: -->
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

## 10. Configuration (appsettings.json)

Add the full resilience configuration block to `OpenRAG.Api/appsettings.json`:

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

**Production override** (`appsettings.Production.json`):

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

## 11. Deployment Considerations

### 11.1 Timeout arithmetic

With the pipeline configured as above, the **worst-case latency** before a caller receives a 503 is:

```
attempts × per-attempt-timeout + sum(backoff delays)
= 4 × 30 s + (1 + 2 + 4) s
= 127 s
```

This replaces the current 300 s blanket timeout. Set `HttpClient.Timeout = 150 s` (as shown in `Program.cs`) as a safety ceiling above this.

For document embedding (large files), the per-attempt timeout may need to be 60 s in production. Use `appsettings.Production.json` to override.

### 11.2 HealthAsync bypass

The `MlClient.HealthAsync()` method is called by the health check on a short `PeriodicTimer`. It must **not** go through the retry/circuit-breaker pipeline (otherwise a single health check could trigger circuit opening).

Two options:
- **Option A**: Register a separate `HttpClient` named `"ml-health"` without Polly for health checks only.
- **Option B**: The health check already catches `BrokenCircuitException` and returns `Degraded` before any HTTP call is made — Polly throws immediately when the circuit is open, so the health check is fast.

Option B works out of the box because the circuit breaker is the outermost strategy: when the circuit is open, it throws `BrokenCircuitException` before the timeout or retry strategies fire. The `HealthAsync()` method's own try/catch in `MlClient` will also catch this and return `false`.

### 11.3 SignalR progress during retry

When `DocumentRetryService` re-indexes a document, it currently does **not** emit SignalR progress events because it doesn't have access to `IHubContext<ProgressHub>`. To add progress:

- Inject `IHubContext<ProgressHub>` into `DocumentRetryService`.
- Emit a `"retry_indexing"` stage event when starting retry and `"done"` / `"failed"` on completion.
- On the frontend, listen for these events in `UploadTab.vue` and update documents with `status === "queued"`.

### 11.4 EF Core migration

After adding `ChunksJson`, `CollectionName`, `RetryCount`, and `RetryAfter` columns to `Document`:

```bash
cd OpenRAG.Api
dotnet ef migrations add AddDocumentRetryColumns
dotnet ef database update
```

The migration will be a purely additive `ALTER TABLE` — no data loss.

### 11.5 ChunksJson storage cost

`ChunksJson` stores the full text of all chunks for a document. For a 100 MB PDF this could be several MB of JSON. Mitigation options:

- Set `doc.ChunksJson = null` once indexing succeeds (already done in the retry service).
- Add a size check: skip storing `ChunksJson` if the serialised length exceeds, e.g., 10 MB (very large documents can simply be re-uploaded if ML was down).
- Move to a blob/file storage in production (S3, Azure Blob).

### 11.6 Kubernetes / Docker considerations

- **Readiness probe**: point to `/api/health`. When ML is degraded, return 200 (so the API pod stays in rotation — it can still serve cached data and queue uploads). Only return 503 when the API itself (DB) is unhealthy.
- **Liveness probe**: a simple `GET /api/health` on a 30 s interval; use `failureThreshold: 3` before killing the pod.
- **ML service readiness**: configure Kubernetes to only route traffic to the ML pod once `/ml/health` returns 200. This prevents the circuit from opening during normal pod startup.

### 11.7 Observability

All three Polly strategies emit named events (`OnRetry`, `OnOpened`, `OnClosed`, `OnHalfOpened`) that log structured messages via `ILogger`. These are compatible with any log sink (Serilog, Application Insights, etc.).

For metrics, add:

```csharp
// In Program.cs, after AddResilienceHandler:
builder.Services.AddOpenTelemetry()
    .WithMetrics(m => m.AddAspNetCoreInstrumentation()
                       .AddHttpClientInstrumentation());
```

Polly v8 automatically emits OpenTelemetry metrics for retry counts, circuit state changes, and timeout counts under the `polly.*` meter namespace.

---

## Summary of New Files

| File | Purpose |
|---|---|
| `OpenRAG.Api/Services/HealthChecks/MlServiceHealthCheck.cs` | `IHealthCheck` that pings `/ml/health` |
| `OpenRAG.Api/Services/HealthChecks/HealthCheckResponseWriter.cs` | JSON formatter for `/api/health` |
| `OpenRAG.Api/Services/Background/DocumentRetryService.cs` | `BackgroundService` retrying failed documents |
| `frontend/src/api/errors.ts` | Shared 503 parsing utilities for Vue |

## Summary of Modified Files

| File | Change |
|---|---|
| `OpenRAG.Api/Program.cs` | Polly pipeline, health checks, hosted service registration |
| `OpenRAG.Api/Controllers/SearchController.cs` | Catch `BrokenCircuitException`, return 503 |
| `OpenRAG.Api/Controllers/DocumentsController.cs` | Catch `BrokenCircuitException`, return 503 with retry hint |
| `OpenRAG.Api/Controllers/ChatController.cs` | Catch `BrokenCircuitException`, return 503 |
| `OpenRAG.Api/Models/Entities/Document.cs` | Add `ChunksJson`, `CollectionName`, `RetryCount`, `RetryAfter` |
| `OpenRAG.Api/Services/DocumentService.cs` | Persist `ChunksJson` + `CollectionName` before ML call |
| `frontend/src/components/SearchTab.vue` | Degraded state display + retry button |
| `frontend/src/components/UploadTab.vue` | Queued state display for 503 uploads |
