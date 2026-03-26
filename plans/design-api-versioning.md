# API Versioning Design — OpenRAG

**Status**: Draft
**Author**: Architect Review
**Date**: 2026-03-26
**Target project**: `OpenRAG.Api` (.NET 8, `net8.0`)

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Versioning Strategy Choice](#2-versioning-strategy-choice)
3. [NuGet Setup — Asp.Versioning.Mvc](#3-nuget-setup--aspversioningmvc)
4. [Program.cs — Full Configuration](#4-programcs--full-configuration)
5. [Controller Migration — SearchController as Template](#5-controller-migration--searchcontroller-as-template)
6. [V2 Planning — Search Enhancements](#6-v2-planning--search-enhancements)
7. [Frontend Versioning](#7-frontend-versioning)
8. [Migration Path — Zero Breaking Changes](#8-migration-path--zero-breaking-changes)
9. [Swagger / OpenAPI per Version](#9-swagger--openapi-per-version)
10. [Deprecation Lifecycle](#10-deprecation-lifecycle)
11. [What NOT to Version](#11-what-not-to-version)
12. [V1 to V2 Transition Roadmap](#12-v1-to-v2-transition-roadmap)

---

## 1. Problem Statement

The current codebase has no versioning layer. All five controllers (`SearchController`, `DocumentsController`, `ChatController`, `CollectionsController`, `HealthController`) expose bare routes like `/api/search` and `/api/documents`. This creates the following hard constraints:

- Any field rename in `SearchRequest` or `SearchResponse` is an immediate breaking change for every caller.
- The frontend (`frontend/src/api/index.ts`) is tightly coupled to the exact JSON shape because `axios.create({ baseURL: '/api' })` holds no version contract.
- Future features — streaming search results, batch ingestion, async job polling — cannot be cleanly introduced without either (a) polluting existing endpoints with optional parameters or (b) silently changing behaviour.
- There is no mechanism to warn a client that a behaviour is going away.

---

## 2. Versioning Strategy Choice

### Options compared

| Approach | URL Path `/api/v1/search` | Header `API-Version: 1` | Query Param `?api-version=1` |
|---|---|---|---|
| Visibility | Explicit — visible in browser, logs, proxies | Hidden — requires inspection of headers | Visible in URL, but awkward to read |
| Cacheability | Excellent — CDN/proxy can cache per path | Poor — must Vary on the header | Acceptable, but query strings invalidate many cache layers |
| REST purity | Debated — some argue versions break resource identity | Closest to REST theory | Common in Azure/Microsoft APIs |
| Ease of implementation | Simple route template change | Requires middleware reads header | Simple filter addition |
| Frontend coupling | Explicit — callers must know version in URL | Callers must set header in every request | Callers must add param to every request |
| Swagger tooling | Best — generates separate docs per prefix naturally | Requires extra configuration | Works but clutters URLs in docs |
| Suitable for internal tools | Yes — straightforward for team members | Yes | Yes |
| Suitable for public APIs | Yes | Yes — cleaner URLs | Less preferred |

### Recommendation: URL Path versioning (`/api/v1/...`)

**Rationale for OpenRAG specifically:**

1. **Internal tool context.** OpenRAG is currently an internal RAG platform with a single bundled Vue frontend and a Python ML service as the only callers of the public API. URL-path versioning imposes the least conceptual overhead for the small team and anyone reading logs or curling endpoints manually.

2. **Zero-friction migration bridge.** The existing unversioned routes (`/api/search`, `/api/documents`, etc.) can be preserved as aliases for v1 by mapping them with `MapToApiVersion("1.0")`. This means adding versioning does not require a single frontend change on day one.

3. **Natural Swagger separation.** Swashbuckle generates separate Swagger UI pages per route prefix automatically when using URL-path versioning, with no filter gymnastics.

4. **Frontend clarity.** When V2 ships with streaming search, the frontend explicitly calls `/api/v2/search` for the new path and `/api/v1/search` for the stable path. The distinction is obvious in code review and network traces.

5. **Proxy and load balancer friendliness.** If OpenRAG ever gains an nginx or Traefik layer, routing rules based on `/api/v1/` or `/api/v2/` are straightforward path matchers that require no header inspection.

**Chosen pattern:** `https://host/api/v{major}/resource`

Minor version (1.0 vs 1.1) is tracked internally for additive, non-breaking changes. Only major version changes appear in the URL prefix.

---

## 3. NuGet Setup — Asp.Versioning.Mvc

### Packages to add

```xml
<!-- OpenRAG.Api/OpenRAG.Api.csproj -->
<ItemGroup>
  <!-- existing packages ... -->
  <PackageReference Include="Asp.Versioning.Mvc"          Version="8.*" />
  <PackageReference Include="Asp.Versioning.Mvc.ApiExplorer" Version="8.*" />
  <!-- Swagger -->
  <PackageReference Include="Swashbuckle.AspNetCore"       Version="6.*" />
</ItemGroup>
```

`Asp.Versioning.Mvc` is the successor to `Microsoft.AspNetCore.Mvc.Versioning` (now archived). It is maintained by the .NET team and supports .NET 8 natively. `Asp.Versioning.Mvc.ApiExplorer` provides `IApiVersionDescriptionProvider` which is required for generating per-version Swagger documents.

**Do not use** the archived `Microsoft.AspNetCore.Mvc.Versioning` — it has not had a release since 2022 and has unresolved .NET 8 compatibility issues.

---

## 4. Program.cs — Full Configuration

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Program.cs`

```csharp
using Asp.Versioning;
using Asp.Versioning.ApiExplorer;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using Microsoft.OpenApi.Models;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Chunking;
using Swashbuckle.AspNetCore.SwaggerGen;

var builder = WebApplication.CreateBuilder(args);

// ── Database ──────────────────────────────────────────────────────────────
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString("Default")
        ?? "Data Source=../data/openrag.db"));

// ── ML Service HTTP client ────────────────────────────────────────────────
builder.Services.AddHttpClient<MlClient>(client =>
{
    client.BaseAddress = new Uri(builder.Configuration["MlService:BaseUrl"] ?? "http://localhost:8001");
    client.Timeout = TimeSpan.FromSeconds(300);
});

// ── LLM client (optional) ─────────────────────────────────────────────────
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

// ── SignalR ───────────────────────────────────────────────────────────────
builder.Services.AddSignalR();

// ── API Versioning ────────────────────────────────────────────────────────
builder.Services.AddApiVersioning(options =>
{
    // Clients that send no version indicator are assumed to target v1.0.
    // This keeps all existing /api/search, /api/documents, etc. working
    // without any change to the frontend.
    options.DefaultApiVersion = new ApiVersion(1, 0);
    options.AssumeDefaultVersionWhenUnspecified = true;

    // Include api-supported-versions and api-deprecated-versions headers
    // in every response so clients can self-discover the version landscape.
    options.ReportApiVersions = true;

    // Primary strategy: URL path segment (/api/v1/, /api/v2/)
    // Secondary strategy: header (API-Version: 1.0) — useful for tooling
    // that cannot modify URL paths.
    options.ApiVersionReader = ApiVersionReader.Combine(
        new UrlSegmentApiVersionReader(),
        new HeaderApiVersionReader("API-Version")
    );
})
.AddMvc()                   // integrate with ASP.NET Core MVC controller discovery
.AddApiExplorer(options =>
{
    // Format the version string as "v1", "v2" in the Swagger dropdown.
    options.GroupNameFormat = "'v'VVV";

    // Substitute the {version} route parameter in [Route] templates
    // so controllers do not have to hard-code it.
    options.SubstituteApiVersionInUrl = true;
});

// ── Controllers + CORS ───────────────────────────────────────────────────
builder.Services.AddControllers();
builder.Services.AddCors(o => o.AddDefaultPolicy(p =>
    p.WithOrigins("http://localhost:5173", "http://localhost:8000")
     .AllowAnyMethod()
     .AllowAnyHeader()
     .AllowCredentials()));

// ── Swagger / OpenAPI (per-version documents) ─────────────────────────────
builder.Services.AddTransient<IConfigureOptions<SwaggerGenOptions>, ConfigureSwaggerOptions>();
builder.Services.AddSwaggerGen(options =>
{
    // Include XML doc comments if you add <GenerateDocumentationFile>true</GenerateDocumentationFile>
    // to the .csproj.  Safe to leave in even before the file exists.
    var xmlFile = $"{System.Reflection.Assembly.GetExecutingAssembly().GetName().Name}.xml";
    var xmlPath = Path.Combine(AppContext.BaseDirectory, xmlFile);
    if (File.Exists(xmlPath))
        options.IncludeXmlComments(xmlPath);
});

var app = builder.Build();

// ── Migrate DB on startup ─────────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    db.Database.Migrate();
}

app.UseCors();

// ── Swagger UI ────────────────────────────────────────────────────────────
if (app.Environment.IsDevelopment())
{
    app.UseSwagger();

    // Render one Swagger UI endpoint per discovered API version.
    var apiVersionDescProvider = app.Services.GetRequiredService<IApiVersionDescriptionProvider>();
    app.UseSwaggerUI(options =>
    {
        foreach (var description in apiVersionDescProvider.ApiVersionDescriptions)
        {
            var label = description.IsDeprecated
                ? $"OpenRAG API {description.GroupName} (deprecated)"
                : $"OpenRAG API {description.GroupName}";
            options.SwaggerEndpoint($"/swagger/{description.GroupName}/swagger.json", label);
        }
        options.RoutePrefix = "swagger";
    });
}

// ── Static files (Vue build output) ──────────────────────────────────────
app.UseDefaultFiles();
app.UseStaticFiles();

app.MapControllers();
app.MapHub<ProgressHub>("/ws/progress");

// ── SPA fallback (Vue Router history mode) ────────────────────────────────
app.MapFallbackToFile("index.html");

app.Run();
```

### ConfigureSwaggerOptions helper class

Place this in `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Infrastructure/ConfigureSwaggerOptions.cs`:

```csharp
using Asp.Versioning.ApiExplorer;
using Microsoft.Extensions.Options;
using Microsoft.OpenApi.Models;
using Swashbuckle.AspNetCore.SwaggerGen;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Dynamically creates one Swagger document per discovered API version.
/// Executed at startup after all API versions are registered.
/// </summary>
public sealed class ConfigureSwaggerOptions(IApiVersionDescriptionProvider provider)
    : IConfigureOptions<SwaggerGenOptions>
{
    public void Configure(SwaggerGenOptions options)
    {
        foreach (var description in provider.ApiVersionDescriptions)
        {
            options.SwaggerDoc(description.GroupName, CreateInfoForVersion(description));
        }
    }

    private static OpenApiInfo CreateInfoForVersion(ApiVersionDescription description)
    {
        var info = new OpenApiInfo
        {
            Title       = "OpenRAG API",
            Version     = description.ApiVersion.ToString(),
            Description = "Retrieval-Augmented Generation document search and chat service.",
            Contact = new OpenApiContact
            {
                Name = "OpenRAG Team",
            },
        };

        if (description.IsDeprecated)
        {
            info.Description +=
                "\n\n**This API version is deprecated.** " +
                "It will be removed 6 months after the deprecation date. " +
                "Please migrate to the latest version.";
        }

        return info;
    }
}
```

---

## 5. Controller Migration — SearchController as Template

This section demonstrates the complete migration of `SearchController`. Apply the same pattern to all other controllers.

### Key decisions in this migration

- The **legacy route** (`/api/search`) is preserved via a second `[Route]` attribute so the Vue frontend does not break.
- The **versioned route** (`/api/v1/search`) is the canonical going-forward path.
- Both routes map to the same controller class and the same action method — no code duplication.
- The `[MapToApiVersion]` attribute pins a specific action to a specific version when a controller supports multiple versions.

### Updated SearchController (V1)

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Controllers/V1/SearchController.cs`

```csharp
using Asp.Versioning;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers.V1;

/// <summary>
/// Document search endpoint — V1.
/// Supports semantic and hybrid retrieval with optional LLM answer generation.
/// </summary>
[ApiController]
[ApiVersion("1.0")]
// Canonical versioned route — new clients should use this.
[Route("api/v{version:apiVersion}/search")]
// Legacy unversioned route — preserved for backward compatibility.
// Requests to /api/search are treated as v1.0 (AssumeDefaultVersionWhenUnspecified = true).
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
{
    /// <summary>
    /// Search documents using the configured retrieval strategy.
    /// </summary>
    /// <param name="req">Search parameters.</param>
    /// <param name="ct">Cancellation token.</param>
    /// <returns>Ranked list of document chunks, optionally with an LLM-generated answer.</returns>
    [HttpPost]
    [MapToApiVersion("1.0")]
    [ProducesResponseType(typeof(SearchResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> Search(
        [FromBody] SearchRequest req,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        var results = await ml.SearchAsync(
            req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            return Ok(new SearchResponse(
                req.Query, results, results.Count,
                generated?.Answer, generated?.Citations));
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }
}
```

### Notes on the dual-route pattern

When `Asp.Versioning` sees a request to `/api/search` (no version segment), it applies the default version (1.0) because `AssumeDefaultVersionWhenUnspecified = true`. The `UrlSegmentApiVersionReader` reads the `{version:apiVersion}` constraint from the route template, but because the unversioned route has no such segment, the default kicks in. Both routes resolve to the same action. The response headers will include:

```
api-supported-versions: 1.0
```

This tells the frontend (or any other client) that v1.0 exists and the legacy path is actually v1.0 under the hood.

### Applying the same pattern to all other controllers

| Original file | New location | Notes |
|---|---|---|
| `Controllers/SearchController.cs` | `Controllers/V1/SearchController.cs` | Template above |
| `Controllers/DocumentsController.cs` | `Controllers/V1/DocumentsController.cs` | Same dual-route pattern |
| `Controllers/ChatController.cs` | `Controllers/V1/ChatController.cs` | Same dual-route pattern |
| `Controllers/CollectionsController.cs` | `Controllers/V1/CollectionsController.cs` | Same dual-route pattern |
| `Controllers/HealthController.cs` | `Controllers/HealthController.cs` | Health is version-neutral — see section 11 |

The move to a `V1/` subfolder is organisational only. C# namespaces (`namespace OpenRAG.Api.Controllers.V1`) are updated but the ASP.NET Core controller discovery is namespace-independent.

---

## 6. V2 Planning — Search Enhancements

V2 introduces three new capabilities that cannot be cleanly bolted onto V1 endpoints without altering their contract:

### 6.1 Feature summary

| Feature | Why V1 cannot absorb it | V2 approach |
|---|---|---|
| **Streaming search results via SSE** | V1 returns a complete `SearchResponse` JSON blob. Streaming requires `Content-Type: text/event-stream` and a chunked response body — incompatible with the existing response shape. | `POST /api/v2/search/stream` |
| **Batch queries** | Accepting `queries: string[]` instead of `query: string` is a breaking schema change. | `POST /api/v2/search/batch` |
| **Async job pattern** | For slow operations (reranking + generation on large collections), returns a `jobId` immediately and exposes a poll endpoint. V1 callers expect a synchronous response. | `POST /api/v2/search/jobs` + `GET /api/v2/search/jobs/{jobId}` |

### 6.2 V2 Request/Response models

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Models/Dto/Requests/V2/SearchV2Request.cs`

```csharp
using System.ComponentModel.DataAnnotations;

namespace OpenRAG.Api.Models.Dto.Requests.V2;

/// <summary>V2 single-query search request.</summary>
public record SearchV2Request(
    [Required, MaxLength(5000)] string Query,
    string Collection = "documents",
    [Range(1, 100)] int TopK = 5,
    bool UseReranker = false,
    string SearchMode = "semantic",
    bool Generate = false,
    /// <summary>
    /// When true, the endpoint streams SSE events instead of returning
    /// a single JSON body.  Requires Accept: text/event-stream.
    /// </summary>
    bool Stream = false
);

/// <summary>V2 batch search request — multiple queries in one round-trip.</summary>
public record BatchSearchRequest(
    [Required, MinLength(1), MaxLength(20)] List<string> Queries,
    string Collection = "documents",
    [Range(1, 100)] int TopK = 5,
    bool UseReranker = false,
    string SearchMode = "semantic"
);
```

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Models/Dto/Responses/V2/SearchV2Response.cs`

```csharp
namespace OpenRAG.Api.Models.Dto.Responses.V2;

/// <summary>
/// V2 search result — adds source document metadata at the top level
/// and separates the answer into a structured object.
/// </summary>
public record ChunkResultV2(
    string Text,
    double Score,
    Dictionary<string, object> Metadata,
    double? RerankScore = null,
    /// <summary>Human-readable source label assembled from metadata.</summary>
    string? SourceLabel = null
);

public record GeneratedAnswer(string Text, List<int> Citations, double ConfidenceScore);

public record SearchV2Response(
    string Query,
    List<ChunkResultV2> Results,
    int Total,
    /// <summary>Null when Generate = false or LLM is not configured.</summary>
    GeneratedAnswer? Answer = null
);

/// <summary>Batch response — one SearchV2Response entry per query.</summary>
public record BatchSearchResponse(List<BatchSearchItem> Items, int TotalQueries);

public record BatchSearchItem(string Query, List<ChunkResultV2> Results, int Total);

/// <summary>Returned immediately by the async job endpoint.</summary>
public record SearchJobAccepted(
    Guid JobId,
    string StatusUrl,
    string Status = "queued"
);

/// <summary>Polled via GET /api/v2/search/jobs/{jobId}.</summary>
public record SearchJobResult(
    Guid JobId,
    string Status,           // queued | running | completed | failed
    SearchV2Response? Result = null,
    string? Error = null,
    DateTimeOffset? CompletedAt = null
);
```

### 6.3 V2 SearchController skeleton

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Controllers/V2/SearchController.cs`

```csharp
using Asp.Versioning;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests.V2;
using OpenRAG.Api.Models.Dto.Responses.V2;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.V2;

namespace OpenRAG.Api.Controllers.V2;

/// <summary>
/// Document search endpoint — V2.
/// Adds streaming (SSE), batch queries, and async job pattern.
/// </summary>
[ApiController]
[ApiVersion("2.0")]
[Route("api/v{version:apiVersion}/search")]
public class SearchController(
    MlClient ml,
    LlmClient llm,
    SearchJobService jobService    // new V2-only service
) : ControllerBase
{
    // ── Standard (synchronous) search — same semantics as V1 ────────────────
    // Non-streaming callers use this. The response shape has changed:
    // ChunkResultV2 adds SourceLabel; Answer is a structured object.

    /// <summary>Single synchronous search query.</summary>
    [HttpPost]
    [MapToApiVersion("2.0")]
    [ProducesResponseType(typeof(SearchV2Response), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> Search(
        [FromBody] SearchV2Request req,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        if (req.Stream)
            return BadRequest(new { detail = "Use POST /search/stream for streaming." });

        var results = await ml.SearchAsync(
            req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);

        var v2Results = results.Select(r => new ChunkResultV2(
            r.Text, r.Score, r.Metadata, r.RerankScore,
            SourceLabel: BuildSourceLabel(r.Metadata)
        )).ToList();

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            var answer = generated is null ? null
                : new GeneratedAnswer(generated.Answer!, generated.Citations ?? [], 0.0);
            return Ok(new SearchV2Response(req.Query, v2Results, v2Results.Count, answer));
        }

        return Ok(new SearchV2Response(req.Query, v2Results, v2Results.Count));
    }

    // ── SSE streaming search ─────────────────────────────────────────────────
    // The client sends Accept: text/event-stream. The server streams
    // individual chunk results as SSE events as they are retrieved,
    // followed by a final "done" event with the generated answer (if any).
    //
    // SSE event format per chunk:
    //   event: chunk
    //   data: { "text": "...", "score": 0.92, "metadata": {...} }
    //
    // Final event:
    //   event: done
    //   data: { "answer": "...", "citations": [1, 2] }

    /// <summary>
    /// Streaming search via Server-Sent Events.
    /// Set Accept: text/event-stream header. Results are pushed as they arrive.
    /// </summary>
    [HttpPost("stream")]
    [MapToApiVersion("2.0")]
    [Produces("text/event-stream")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task StreamSearch(
        [FromBody] SearchV2Request req,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
        {
            Response.StatusCode = StatusCodes.Status400BadRequest;
            return;
        }

        Response.Headers.ContentType = "text/event-stream";
        Response.Headers.CacheControl = "no-cache";
        Response.Headers.Connection   = "keep-alive";

        await Response.Body.FlushAsync(ct);

        var results = await ml.SearchAsync(
            req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);

        foreach (var chunk in results)
        {
            var v2Chunk = new ChunkResultV2(
                chunk.Text, chunk.Score, chunk.Metadata, chunk.RerankScore,
                SourceLabel: BuildSourceLabel(chunk.Metadata));

            var json = System.Text.Json.JsonSerializer.Serialize(v2Chunk);
            await WriteSSEEventAsync("chunk", json, ct);
        }

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            if (generated is not null)
            {
                var doneJson = System.Text.Json.JsonSerializer.Serialize(new
                {
                    answer    = generated.Answer,
                    citations = generated.Citations,
                });
                await WriteSSEEventAsync("done", doneJson, ct);
            }
        }
        else
        {
            await WriteSSEEventAsync("done", "{}", ct);
        }
    }

    // ── Batch search ──────────────────────────────────────────────────────────
    // Accepts up to 20 queries and returns results for all of them.
    // Each query runs independently against the ML service.
    // Consider adding a concurrency limit (SemaphoreSlim) for production.

    /// <summary>
    /// Execute multiple search queries in a single request.
    /// Maximum 20 queries per batch.
    /// </summary>
    [HttpPost("batch")]
    [MapToApiVersion("2.0")]
    [ProducesResponseType(typeof(BatchSearchResponse), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> BatchSearch(
        [FromBody] BatchSearchRequest req,
        CancellationToken ct = default)
    {
        if (req.Queries is null || req.Queries.Count == 0)
            return BadRequest(new { detail = "At least one query is required." });

        if (req.Queries.Count > 20)
            return BadRequest(new { detail = "Batch limited to 20 queries." });

        var tasks = req.Queries.Select(q => ml.SearchAsync(
            q, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct));

        var allResults = await Task.WhenAll(tasks);

        var items = req.Queries.Zip(allResults, (query, results) =>
        {
            var v2Results = results.Select(r => new ChunkResultV2(
                r.Text, r.Score, r.Metadata, r.RerankScore,
                SourceLabel: BuildSourceLabel(r.Metadata)
            )).ToList();
            return new BatchSearchItem(query, v2Results, v2Results.Count);
        }).ToList();

        return Ok(new BatchSearchResponse(items, items.Count));
    }

    // ── Async job search ──────────────────────────────────────────────────────
    // For long-running searches (large collections, slow reranking + generation).
    // The client receives a jobId immediately and polls for completion.
    // SearchJobService must be implemented as a background queue (e.g., Channel<T>
    // backed by a hosted service or Hangfire for persistence across restarts).

    /// <summary>
    /// Submit a search as an async job. Returns immediately with a jobId.
    /// Poll GET /api/v2/search/jobs/{jobId} for results.
    /// </summary>
    [HttpPost("jobs")]
    [MapToApiVersion("2.0")]
    [ProducesResponseType(typeof(SearchJobAccepted), StatusCodes.Status202Accepted)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    public async Task<IActionResult> SubmitJob(
        [FromBody] SearchV2Request req,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        var jobId    = await jobService.EnqueueAsync(req, ct);
        var statusUrl = Url.Action(nameof(GetJobResult), new { jobId })!;

        return Accepted(statusUrl, new SearchJobAccepted(jobId, statusUrl));
    }

    /// <summary>Poll for the result of an async search job.</summary>
    [HttpGet("jobs/{jobId:guid}")]
    [MapToApiVersion("2.0")]
    [ProducesResponseType(typeof(SearchJobResult), StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public async Task<IActionResult> GetJobResult(Guid jobId, CancellationToken ct = default)
    {
        var result = await jobService.GetResultAsync(jobId, ct);
        if (result is null) return NotFound(new { detail = $"Job {jobId} not found." });
        return Ok(result);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static string BuildSourceLabel(Dictionary<string, object> metadata)
    {
        var filename = metadata.TryGetValue("filename", out var f) ? f?.ToString() : null;
        var section  = metadata.TryGetValue("section",  out var s) ? s?.ToString() : null;
        return string.IsNullOrEmpty(section) ? (filename ?? "Unknown") : $"{filename} › {section}";
    }

    private async Task WriteSSEEventAsync(string eventName, string data, CancellationToken ct)
    {
        var writer = new System.IO.StreamWriter(Response.Body, leaveOpen: true);
        await writer.WriteAsync($"event: {eventName}\n");
        await writer.WriteAsync($"data: {data}\n\n");
        await writer.FlushAsync(ct);
    }
}
```

### 6.4 SearchJobService stub

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Services/V2/SearchJobService.cs`

```csharp
using OpenRAG.Api.Models.Dto.Requests.V2;
using OpenRAG.Api.Models.Dto.Responses.V2;

namespace OpenRAG.Api.Services.V2;

/// <summary>
/// Manages async search jobs. Replace the in-memory dictionary with
/// a persistent store (e.g., SQLite table or Redis) before production use.
/// </summary>
public sealed class SearchJobService(MlClient ml, LlmClient llm, ILogger<SearchJobService> logger)
{
    // Keyed by jobId. In production, persist in SQLite or an external queue.
    private readonly Dictionary<Guid, SearchJobResult> _jobs = new();
    private readonly SemaphoreSlim _lock = new(1, 1);

    public async Task<Guid> EnqueueAsync(SearchV2Request req, CancellationToken ct = default)
    {
        var jobId = Guid.NewGuid();

        await _lock.WaitAsync(ct);
        _jobs[jobId] = new SearchJobResult(jobId, "queued");
        _lock.Release();

        // Fire-and-forget on the thread pool.
        // In production: use IHostedService + Channel<T>, or Hangfire.
        _ = Task.Run(() => ExecuteJobAsync(jobId, req), CancellationToken.None);

        return jobId;
    }

    public async Task<SearchJobResult?> GetResultAsync(Guid jobId, CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct);
        var result = _jobs.TryGetValue(jobId, out var job) ? job : null;
        _lock.Release();
        return result;
    }

    private async Task ExecuteJobAsync(Guid jobId, SearchV2Request req)
    {
        await UpdateStatusAsync(jobId, "running");
        try
        {
            var results = await ml.SearchAsync(
                req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode);

            var v2Results = results.Select(r => new ChunkResultV2(
                r.Text, r.Score, r.Metadata, r.RerankScore)).ToList();

            SearchV2Response response;
            if (req.Generate && llm.IsEnabled)
            {
                var generated = await llm.GenerateAsync(req.Query, results);
                var answer = generated is null ? null
                    : new GeneratedAnswer(generated.Answer!, generated.Citations ?? [], 0.0);
                response = new SearchV2Response(req.Query, v2Results, v2Results.Count, answer);
            }
            else
            {
                response = new SearchV2Response(req.Query, v2Results, v2Results.Count);
            }

            await UpdateResultAsync(jobId, response);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Search job {JobId} failed", jobId);
            await UpdateErrorAsync(jobId, ex.Message);
        }
    }

    private async Task UpdateStatusAsync(Guid jobId, string status)
    {
        await _lock.WaitAsync();
        if (_jobs.TryGetValue(jobId, out var existing))
            _jobs[jobId] = existing with { Status = status };
        _lock.Release();
    }

    private async Task UpdateResultAsync(Guid jobId, SearchV2Response result)
    {
        await _lock.WaitAsync();
        if (_jobs.TryGetValue(jobId, out var existing))
            _jobs[jobId] = existing with { Status = "completed", Result = result, CompletedAt = DateTimeOffset.UtcNow };
        _lock.Release();
    }

    private async Task UpdateErrorAsync(Guid jobId, string error)
    {
        await _lock.WaitAsync();
        if (_jobs.TryGetValue(jobId, out var existing))
            _jobs[jobId] = existing with { Status = "failed", Error = error, CompletedAt = DateTimeOffset.UtcNow };
        _lock.Release();
    }
}
```

Register in `Program.cs`:

```csharp
builder.Services.AddSingleton<SearchJobService>();
```

---

## 7. Frontend Versioning

File: `d:/Works/trgiangvp3/open-rag/frontend/src/api/index.ts`

The strategy is:

1. Keep the existing `api` instance (baseURL `/api`) as-is. All current function calls continue to work without modification. This is the **V1 compatibility layer**.
2. Add an `apiV1` alias (explicit) and an `apiV2` instance pointing to `/api/v2`.
3. Export version-specific function groups. The V1 group is the existing functions, untouched. The V2 group introduces new signatures.
4. The `versionedApi` factory allows calling an arbitrary version if needed.

```typescript
import axios, { type AxiosInstance } from 'axios'

// ── Axios instances ────────────────────────────────────────────────────────

/**
 * Legacy unversioned client — maps to /api (treated as v1 by the server).
 * Keep using this for all existing code.  Do NOT remove.
 */
export const api = axios.create({ baseURL: '/api' })

/**
 * Explicit V1 client — equivalent to `api` but uses the canonical versioned path.
 * Prefer this in new V1 code so the intent is clear.
 */
export const apiV1 = axios.create({ baseURL: '/api/v1' })

/**
 * V2 client — points to the V2 endpoints (streaming, batch, async jobs).
 * Only use functions exported from the `v2` namespace.
 */
export const apiV2 = axios.create({ baseURL: '/api/v2' })

/**
 * Factory for creating a versioned API client on demand.
 * Useful for components that need to switch versions at runtime.
 */
export function versionedApi(version: 1 | 2): AxiosInstance {
  return version === 2 ? apiV2 : apiV1
}

// ── Shared types ───────────────────────────────────────────────────────────

export interface ChunkResult {
  text: string
  score: number
  rerankScore?: number
  metadata: Record<string, unknown>
}

export interface SearchResponse {
  query: string
  results: ChunkResult[]
  total: number
  answer?: string
  citations?: number[]
}

export interface DocumentInfo {
  id: string
  filename: string
  collection: string
  chunkCount: number
  createdAt: string
}

export interface DocumentListResponse {
  documents: DocumentInfo[]
  total: number
}

export interface CollectionInfo {
  name: string
  description: string
  documentCount: number
  chunkCount: number
}

export interface IngestResponse {
  documentId: string
  filename: string
  chunkCount: number
  message: string
}

export interface StatusResponse {
  status: string
  message: string
}

export interface SearchOptions {
  useReranker?: boolean
  searchMode?: 'semantic' | 'hybrid'
  generate?: boolean
}

export interface ChatRequest {
  query: string
  collection?: string
  sessionId?: string
  topK?: number
  useReranker?: boolean
  searchMode?: string
}

export interface ChatResponse {
  sessionId: string
  answer?: string
  citations?: number[]
  chunks: ChunkResult[]
}

export interface ChatHistoryResponse {
  sessionId: string
  messages: { role: string; content: string }[]
}

// ── V2-only types ──────────────────────────────────────────────────────────

export interface ChunkResultV2 extends ChunkResult {
  sourceLabel?: string
}

export interface GeneratedAnswer {
  text: string
  citations: number[]
  confidenceScore: number
}

export interface SearchV2Response {
  query: string
  results: ChunkResultV2[]
  total: number
  answer?: GeneratedAnswer
}

export interface BatchSearchRequest {
  queries: string[]
  collection?: string
  topK?: number
  useReranker?: boolean
  searchMode?: 'semantic' | 'hybrid'
}

export interface BatchSearchItem {
  query: string
  results: ChunkResultV2[]
  total: number
}

export interface BatchSearchResponse {
  items: BatchSearchItem[]
  totalQueries: number
}

export interface SearchJobAccepted {
  jobId: string
  statusUrl: string
  status: 'queued'
}

export interface SearchJobResult {
  jobId: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  result?: SearchV2Response
  error?: string
  completedAt?: string
}

// ── V1 API functions (unchanged from current implementation) ───────────────
// All calls go through the legacy `api` client so existing components
// require zero modification.

export const search = (
  query: string,
  collection: string,
  topK: number,
  opts: SearchOptions = {}
) => api.post<SearchResponse>('/search', { query, collection, topK, ...opts })

export const uploadFile = (file: File, collection: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('collection', collection)
  return api.post<IngestResponse>('/documents/upload', form)
}

export const ingestText = (text: string, title: string, collection: string) => {
  const form = new FormData()
  form.append('text', text)
  form.append('title', title)
  form.append('collection', collection)
  return api.post<IngestResponse>('/documents/text', form)
}

export const listDocuments = (collection: string) =>
  api.get<DocumentListResponse>('/documents', { params: { collection } })

export const deleteDocument = (id: string, collection: string) =>
  api.delete<StatusResponse>(`/documents/${id}`, { params: { collection } })

export const listCollections = () =>
  api.get<CollectionInfo[]>('/collections')

export const createCollection = (name: string, description: string) =>
  api.post<StatusResponse>('/collections', { name, description })

export const deleteCollection = (name: string) =>
  api.delete<StatusResponse>(`/collections/${name}`)

export const health = () =>
  api.get<{ status: string }>('/health')

export const chat = (req: ChatRequest) =>
  api.post<ChatResponse>('/chat', req)

export const getChatHistory = (sessionId: string) =>
  api.get<ChatHistoryResponse>(`/chat/${sessionId}/history`)

export const deleteChatSession = (sessionId: string) =>
  api.delete(`/chat/${sessionId}`)

// ── V2 API functions ───────────────────────────────────────────────────────

export const v2 = {
  /** Standard (synchronous) V2 search with richer response shape. */
  search: (
    query: string,
    collection: string,
    topK: number,
    opts: SearchOptions = {}
  ) => apiV2.post<SearchV2Response>('/search', { query, collection, topK, ...opts }),

  /**
   * Streaming SSE search.
   * Returns a native EventSource — the caller attaches event listeners.
   * Note: EventSource only supports GET; use fetchEventSource from
   * @microsoft/fetch-event-source for POST-based SSE.
   *
   * Usage:
   *   const source = v2.searchStream({ query: '...', collection: 'documents', topK: 5 })
   *   source.addEventListener('chunk', (e) => { const chunk = JSON.parse(e.data) })
   *   source.addEventListener('done',  (e) => { const answer = JSON.parse(e.data) })
   */
  searchStream: (req: {
    query: string
    collection: string
    topK: number
    useReranker?: boolean
    searchMode?: 'semantic' | 'hybrid'
    generate?: boolean
  }): AbortController => {
    const controller = new AbortController()

    // Use fetch directly for POST + SSE.
    fetch('/api/v2/search/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(req),
      signal: controller.signal,
    }).then(async (res) => {
      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        // Raw SSE text — parse and dispatch custom events on window
        const text = decoder.decode(value)
        // (parsing logic delegated to the consuming component)
        window.dispatchEvent(new CustomEvent('rag:sse', { detail: text }))
      }
    })

    return controller
  },

  /** Batch search — up to 20 queries in one request. */
  batchSearch: (req: BatchSearchRequest) =>
    apiV2.post<BatchSearchResponse>('/search/batch', req),

  /** Submit an async search job. Poll getJob() for results. */
  submitJob: (
    query: string,
    collection: string,
    topK: number,
    opts: SearchOptions = {}
  ) => apiV2.post<SearchJobAccepted>('/search/jobs', { query, collection, topK, ...opts }),

  /** Poll for an async search job result. */
  getJob: (jobId: string) =>
    apiV2.get<SearchJobResult>(`/search/jobs/${jobId}`),
}
```

---

## 8. Migration Path — Zero Breaking Changes

This section shows exactly how versioning is added without touching any route that the current Vue frontend uses.

### Phase 1 — Add versioning infrastructure (no visible change to clients)

1. Add NuGet packages to `OpenRAG.Api.csproj`.
2. Update `Program.cs` with the `AddApiVersioning` / `AddMvc` / `AddApiExplorer` calls shown in section 4.
3. Move each controller to a `V1/` subfolder and update its namespace.
4. Add both `[Route("api/v{version:apiVersion}/resource")]` and `[Route("api/resource")]` attributes to each controller.
5. Add `[ApiVersion("1.0")]` to each controller.
6. Deploy. Verify:
   - `POST /api/search` — still works, returns HTTP 200.
   - Response headers now include `api-supported-versions: 1.0`.
   - `POST /api/v1/search` — also works (new canonical path).
   - Swagger UI at `/swagger` shows one document: "OpenRAG API v1".

### Phase 2 — Introduce V2 controllers (additive only)

1. Add V2 controllers in `Controllers/V2/`.
2. Add V2 service (`SearchJobService`) to `Program.cs`.
3. Deploy. Verify:
   - All V1 routes still return HTTP 200 — no regression.
   - `POST /api/v2/search` works.
   - `POST /api/v2/search/stream` works (SSE).
   - Swagger UI shows two documents: "OpenRAG API v1" and "OpenRAG API v2".
   - Response headers include `api-supported-versions: 1.0, 2.0`.

### Phase 3 — Frontend migration to V2 (optional, incremental)

Update `frontend/src/api/index.ts` as shown in section 7. Because the existing `search()`, `uploadFile()`, etc. functions are unchanged and still call `api` (pointing to `/api`), no Vue component needs modification at this point. V2 functions are additive new exports.

Migrate individual Vue components to use `v2.search()` or `v2.searchStream()` when the feature is ready for the user-facing build.

### Dual-route declaration reference

Every controller follows this exact pattern. The unversioned route is the backward-compatible alias; the versioned route is canonical going forward.

```csharp
[ApiController]
[ApiVersion("1.0")]
[Route("api/v{version:apiVersion}/documents")]  // canonical: /api/v1/documents
[Route("api/documents")]                         // legacy alias: /api/documents
public class DocumentsController(...) : ControllerBase { ... }
```

```csharp
[ApiController]
[ApiVersion("1.0")]
[Route("api/v{version:apiVersion}/chat")]
[Route("api/chat")]
public class ChatController(...) : ControllerBase { ... }
```

```csharp
[ApiController]
[ApiVersion("1.0")]
[Route("api/v{version:apiVersion}/collections")]
[Route("api/collections")]
public class CollectionsController(...) : ControllerBase { ... }
```

---

## 9. Swagger / OpenAPI per Version

The `ConfigureSwaggerOptions` class shown in section 4 handles per-version document generation. Here is the complete Swagger pipeline for clarity.

### What gets generated

After adding both V1 and V2 controllers:

| URL | Content |
|---|---|
| `/swagger/v1/swagger.json` | OpenAPI document for all `[ApiVersion("1.0")]` controllers |
| `/swagger/v2/swagger.json` | OpenAPI document for all `[ApiVersion("2.0")]` controllers |
| `/swagger` | Swagger UI with a dropdown to switch between v1 and v2 |

### Swagger document filter to hide deprecated operations (optional)

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Infrastructure/DeprecatedOperationFilter.cs`

```csharp
using Microsoft.OpenApi.Models;
using Swashbuckle.AspNetCore.SwaggerGen;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Marks operations as deprecated in the OpenAPI document when the
/// controller method is decorated with [Obsolete].
/// </summary>
public sealed class DeprecatedOperationFilter : IOperationFilter
{
    public void Apply(OpenApiOperation operation, OperationFilterContext context)
    {
        var isObsolete = context.MethodInfo
            .GetCustomAttributes(typeof(ObsoleteAttribute), true)
            .Length > 0;

        if (isObsolete)
            operation.Deprecated = true;
    }
}
```

Register in `Program.cs` inside `AddSwaggerGen`:

```csharp
builder.Services.AddSwaggerGen(options =>
{
    options.OperationFilter<DeprecatedOperationFilter>();
    // ... existing options
});
```

### Sample V1 Swagger JSON shape (abbreviated)

```json
{
  "openapi": "3.0.1",
  "info": {
    "title": "OpenRAG API",
    "version": "1.0"
  },
  "paths": {
    "/api/v1/search": {
      "post": {
        "tags": ["Search"],
        "summary": "Search documents using the configured retrieval strategy.",
        "requestBody": { "content": { "application/json": { "schema": { "$ref": "#/components/schemas/SearchRequest" } } } },
        "responses": {
          "200": { "description": "OK", "content": { "application/json": { "schema": { "$ref": "#/components/schemas/SearchResponse" } } } },
          "400": { "description": "Bad Request" }
        }
      }
    }
  }
}
```

---

## 10. Deprecation Lifecycle

### Policy definition

| Stage | Duration | Action |
|---|---|---|
| **Active** | Indefinite | Version is current. No warnings. |
| **Deprecated** | 6 months minimum | `Deprecated = true` on `[ApiVersion]`. Response header `api-deprecated-versions` appears. Swagger doc annotates the version. `[Obsolete]` on affected controller actions. |
| **Sunset** | After 6-month window | HTTP `410 Gone` or redirect to new version. Optionally add a `Sunset` response header per RFC 8594. |
| **Removed** | Post-sunset | Controller and DTOs deleted from codebase. |

### How to mark V1 as deprecated

When V2 is stable and V1 enters its 6-month deprecation window, update the V1 controllers:

```csharp
// Controllers/V1/SearchController.cs

[ApiController]
[ApiVersion("1.0", Deprecated = true)]   // <-- set Deprecated = true
[Route("api/v{version:apiVersion}/search")]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
{
    /// <summary>
    /// Search documents using the configured retrieval strategy.
    /// DEPRECATED: Migrate to POST /api/v2/search.
    /// This endpoint will be removed on 2027-01-01.
    /// </summary>
    [HttpPost]
    [MapToApiVersion("1.0")]
    [Obsolete("Use POST /api/v2/search. V1 will be removed on 2027-01-01.")]
    public async Task<IActionResult> Search(
        [FromBody] SearchRequest req,
        CancellationToken ct = default)
    {
        // existing implementation unchanged
    }
}
```

Effect on responses after setting `Deprecated = true`:

```
api-supported-versions: 1.0, 2.0
api-deprecated-versions: 1.0
```

The client receives a machine-readable signal that V1 is going away. No action is required in the application logic — `Asp.Versioning` emits the header automatically.

### Adding a Sunset header (RFC 8594)

For clarity, add a response filter that appends the `Sunset` header to all V1 responses during the deprecation period:

```csharp
// Infrastructure/SunsetHeaderFilter.cs
using Asp.Versioning;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Appends a Sunset header (RFC 8594) to responses from deprecated API versions,
/// telling clients the exact date the version will be removed.
/// </summary>
[AttributeUsage(AttributeTargets.Class | AttributeTargets.Method)]
public sealed class SunsetAttribute(string isoDate) : ActionFilterAttribute
{
    public override void OnActionExecuted(ActionExecutedContext context)
    {
        context.HttpContext.Response.Headers["Sunset"] = isoDate;
        context.HttpContext.Response.Headers["Deprecation"] = "true";
        base.OnActionExecuted(context);
    }
}
```

Apply to the deprecated V1 controller:

```csharp
[ApiVersion("1.0", Deprecated = true)]
[Sunset("2027-01-01")]   // clients see: Sunset: 2027-01-01
public class SearchController(...) { ... }
```

### Communication checklist when deprecating a version

- [ ] Set `Deprecated = true` on all `[ApiVersion("1.0")]` attributes.
- [ ] Add `[Obsolete]` to all V1 action methods with migration instructions.
- [ ] Apply `[Sunset("YYYY-MM-DD")]` to all V1 controllers.
- [ ] Update the Swagger description in `ConfigureSwaggerOptions` (already handled — it appends a deprecation notice when `description.IsDeprecated` is true).
- [ ] Announce the sunset date in release notes / changelog.
- [ ] After the sunset date: return `410 Gone` from V1 routes (replace action bodies with `return StatusCode(410, new { detail = "This API version has been removed. Use /api/v2/..." })`).

---

## 11. What NOT to Version

### Internal ML service (`http://localhost:8001`)

The Python FastAPI ML service (`ml_service/`) is an internal implementation detail — a private backend that only `MlClient` in the .NET layer calls. It is not a public API. It should not be versioned with `Asp.Versioning` or any public version contract.

Rationale:
- The ML service interface changes together with `MlClient` in a single deployment unit. There is no independent consumer that needs protection from breaking changes.
- Adding a version to `/ml/search` etc. creates operational overhead with no benefit.
- If the ML service needs to support multiple algorithm versions simultaneously (e.g., bge-m3 vs a future model), use feature flags or configuration parameters inside the existing endpoints, not URL versioning.

### SignalR / WebSocket events (`/ws/progress`)

`ProgressHub` sends `progress` events over SignalR during document indexing. These events are:
- Short-lived (only during a single ingest operation).
- Consumed only by the bundled Vue frontend.
- Not part of any external integration contract.

WebSocket/SignalR event schemas should evolve without version numbers. Breaking changes to the event payload are coordinated by deploying both frontend and backend together (they are served from the same .NET host).

If a future scenario requires a long-running WebSocket contract with external clients, introduce a `hub-version` query parameter at the connection URL level — not through `Asp.Versioning`.

### Health endpoint (`/api/health`)

Health checks (`HealthController`) are consumed by infrastructure tooling (load balancers, Docker health checks, uptime monitors). They must be stable and version-neutral. The endpoint stays at `/api/health` with no version prefix. Apply `[ApiVersionNeutral]` to opt it out of version routing:

```csharp
// Controllers/HealthController.cs
using Asp.Versioning;

[ApiController]
[ApiVersionNeutral]           // responds to all versions and to unversioned requests
[Route("api/health")]
public class HealthController(MlClient ml) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> Health(CancellationToken ct = default)
    {
        var mlOk = await ml.HealthAsync(ct);
        return Ok(new
        {
            status     = mlOk ? "ok" : "degraded",
            ml_service = mlOk ? "ok" : "unavailable",
        });
    }
}
```

---

## 12. V1 to V2 Transition Roadmap

### Timeline (assumes V2 work starts Q2 2026)

```
Q2 2026  ─── Phase 1: Versioning infrastructure (2 weeks)
              Add Asp.Versioning packages.
              Migrate controllers to V1/ subfolder, dual-route.
              Update Program.cs.
              Add Swagger per-version docs.
              ZERO frontend changes required.

Q3 2026  ─── Phase 2: V2 feature implementation (6–8 weeks)
              Implement V2 SearchController (stream, batch, async job).
              Implement V2 SearchJobService.
              Update frontend api/index.ts with v2 namespace.
              Migrate Search tab in Vue to use v2.searchStream() when
              the user enables "Stream results" toggle (feature flag).

Q4 2026  ─── Phase 3: V1 deprecation announcement
              Set Deprecated = true on all V1 controllers.
              Add [Sunset("2027-07-01")] header.
              Announce 6-month deprecation window in changelog.
              Migrate remaining Vue components to V2 functions.
              api-deprecated-versions: 1.0 header appears in all responses.

Q1–Q2 2027 ─ Phase 4: V1 sunset
              After 2027-07-01: replace V1 action bodies with 410 Gone.
              Keep the [Route("api/search")] alias returning 410 Gone for
              any undiscovered V1 callers (do not delete the route until
              traffic logs confirm zero hits).

Q3 2027  ─── Phase 5: V1 removal
              Delete V1 controllers, V1 DTOs.
              Remove legacy unversioned [Route] attributes.
              Remove api-deprecated-versions header logic.
              Tag git commit: api/v1-removed.
```

### Risk mitigation

| Risk | Mitigation |
|---|---|
| Undiscovered clients calling `/api/search` | Legacy routes return HTTP 200 (V1 behaviour) indefinitely until Phase 4. Response header `api-deprecated-versions: 1.0` gives clients advance warning. Monitor access logs for V1 route hits before the sunset date. |
| SSE not supported by some clients | V2 streaming is additive. The synchronous `POST /api/v2/search` provides the same results without streaming. Clients opt in to streaming by calling `POST /api/v2/search/stream`. |
| In-memory `SearchJobService` loses jobs on restart | Acceptable in Phase 2 (development). Before Phase 3 (production), migrate `_jobs` dictionary to a SQLite table backed by an EF Core `DbContext`. |
| V2 response shape incompatible with existing TypeScript types | V2 types (`SearchV2Response`, `ChunkResultV2`, `GeneratedAnswer`) are separate from V1 types in `api/index.ts`. No V1 interface is modified. |

---

## Appendix — File Tree After Full Implementation

```
OpenRAG.Api/
├── Controllers/
│   ├── HealthController.cs          # [ApiVersionNeutral] — unchanged location
│   ├── V1/
│   │   ├── SearchController.cs      # [ApiVersion("1.0")] dual-route
│   │   ├── DocumentsController.cs   # [ApiVersion("1.0")] dual-route
│   │   ├── ChatController.cs        # [ApiVersion("1.0")] dual-route
│   │   └── CollectionsController.cs # [ApiVersion("1.0")] dual-route
│   └── V2/
│       └── SearchController.cs      # [ApiVersion("2.0")] /api/v2/search
├── Infrastructure/
│   ├── ConfigureSwaggerOptions.cs   # per-version Swagger doc generator
│   ├── DeprecatedOperationFilter.cs # marks [Obsolete] ops as deprecated in OAS
│   └── SunsetAttribute.cs          # adds Sunset + Deprecation response headers
├── Models/
│   └── Dto/
│       ├── Requests/
│       │   ├── SearchRequest.cs     # V1 — unchanged
│       │   ├── ChatRequest.cs       # V1 — unchanged
│       │   └── V2/
│       │       └── SearchV2Request.cs
│       └── Responses/
│           ├── SearchResponse.cs    # V1 — unchanged
│           └── V2/
│               └── SearchV2Response.cs
└── Services/
    └── V2/
        └── SearchJobService.cs

frontend/src/api/
└── index.ts                         # updated: v2 namespace, apiV1/apiV2 instances
```
