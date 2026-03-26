# OpenRAG: Search Result Caching — Design Document

**Date**: 2026-03-26
**Status**: Design Proposal
**Target**: `OpenRAG.Api` (.NET 8, ASP.NET Core)

---

## 1. Problem Statement

Every call to `POST /api/search` triggers a chain of expensive ML operations:

| Step | Component | Latency |
|---|---|---|
| Query embedding | GPU inference (sentence-transformers) | 100–500 ms |
| ChromaDB vector search | Disk/memory ANN index | 50–200 ms |
| BM25 hybrid search (optional) | CPU, inverted index | 10–100 ms |
| Cross-encoder reranking (optional) | GPU inference | 200–800 ms |
| **Total** | | **360 ms – 1.6 s** |

Repeated identical queries re-execute the entire pipeline. In a typical RAG application, the same questions are asked frequently (e.g., "What is the refund policy?"), especially in a multi-user environment. This wastes GPU cycles and increases response latency unnecessarily.

`ChatService.cs` also calls `ml.SearchAsync()` for every chat turn, compounding the problem.

### Expected Speedup

| Scenario | Without cache | With cache (hit) |
|---|---|---|
| Warm semantic search | ~350 ms | ~2 ms (memory) / ~5 ms (Redis) |
| Warm reranker search | ~1.2 s | ~2 ms / ~5 ms |
| Cache miss (first call) | ~1.2 s | ~1.2 s + ~1 ms write overhead |
| Embedding reuse only | ~700 ms | ~300 ms (skips embed step) |

**Target**: >80% of repeated queries served from cache at <10 ms.

---

## 2. Solution Architecture

```
                        ┌──────────────────────────────────────────┐
                        │           OpenRAG.Api (.NET 8)            │
                        │                                           │
  HTTP POST /api/search │                                           │
 ───────────────────────►  SearchController                        │
  ?noCache=true bypass  │      │                                   │
                        │      ▼                                   │
                        │  SearchCacheService                       │
                        │      │                                   │
                        │      ├─── Build cache key (SHA-256)      │
                        │      │    normalize(query+params)        │
                        │      │                                   │
                        │      ├─── Check ISearchCache             │
                        │      │         │                         │
                        │      │    ┌────┴─────────────────┐       │
                        │      │    │                       │       │
                        │      │  Redis               MemoryCache   │
                        │      │  (primary)           (fallback)   │
                        │      │    │                       │       │
                        │      │    └────────┬─────────────┘       │
                        │      │             │                      │
                        │      │         HIT │ return cached results│
                        │      │         MISS▼                     │
                        │      │                                   │
                        │      ├─── EmbeddingCacheService          │
                        │      │    check embedding cache (24h TTL)│
                        │      │         │                         │
                        │      │    HIT──►  skip embed call        │
                        │      │    MISS──► MlClient.SearchAsync() │
                        │      │                                   │
                        │      │    ┌──── ML Service (Python) ───┐ │
                        │      │    │  1. embed_query (GPU)      │ │
                        │      │    │  2. ChromaDB search        │ │
                        │      │    │  3. BM25 hybrid (optional) │ │
                        │      │    │  4. rerank (optional, GPU) │ │
                        │      │    └────────────────────────────┘ │
                        │      │                                   │
                        │      └─── Write result to cache (5m TTL) │
                        │           Write embedding to cache (24h) │
                        │                                           │
                        │  DocumentService                         │
                        │      │ on index/delete:                  │
                        │      └─── Invalidate collection cache    │
                        │           (keyed set + DEL)              │
                        └──────────────────────────────────────────┘
```

---

## 3. NuGet Packages

Add to `OpenRAG.Api/OpenRAG.Api.csproj`:

```xml
<PackageReference Include="Microsoft.Extensions.Caching.StackExchangeRedis" Version="8.*" />
<PackageReference Include="Microsoft.Extensions.Caching.Memory" Version="8.*" />
```

No other third-party dependencies are required. `IDistributedCache` is the abstraction used throughout; `IMemoryCache` is used as a local fallback.

---

## 4. Configuration

### 4.1 `appsettings.json`

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
  "Cache": {
    "Redis": {
      "ConnectionString": ""
    },
    "SearchTtlSeconds": 300,
    "EmbeddingTtlSeconds": 86400,
    "CollectionMetaTtlSeconds": 60
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

When `Cache:Redis:ConnectionString` is empty or missing, the system falls back to in-process `IMemoryCache`. No Redis server is required for local development.

### 4.2 `CacheOptions.cs`

**File**: `OpenRAG.Api/Configuration/CacheOptions.cs`

```csharp
namespace OpenRAG.Api.Configuration;

public sealed class CacheOptions
{
    public const string SectionName = "Cache";

    public RedisOptions Redis { get; init; } = new();
    public int SearchTtlSeconds { get; init; } = 300;
    public int EmbeddingTtlSeconds { get; init; } = 86400;
    public int CollectionMetaTtlSeconds { get; init; } = 60;

    public TimeSpan SearchTtl => TimeSpan.FromSeconds(SearchTtlSeconds);
    public TimeSpan EmbeddingTtl => TimeSpan.FromSeconds(EmbeddingTtlSeconds);
    public TimeSpan CollectionMetaTtl => TimeSpan.FromSeconds(CollectionMetaTtlSeconds);
}

public sealed class RedisOptions
{
    public string ConnectionString { get; init; } = "";
}
```

---

## 5. Cache Key Strategy

**File**: `OpenRAG.Api/Services/Cache/CacheKeyFactory.cs`

The cache key must be stable regardless of query whitespace variations or JSON field ordering. A SHA-256 hash of a canonicalized JSON object is used as the key suffix. This produces a fixed-length 64-character hex string regardless of query length.

```csharp
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace OpenRAG.Api.Services.Cache;

/// <summary>
/// Produces deterministic, collision-resistant cache keys for search parameters
/// and embedding vectors.
/// </summary>
public static class CacheKeyFactory
{
    private static readonly JsonSerializerOptions CanonicalJson = new()
    {
        // Alphabetical property ordering ensures field-order independence.
        // System.Text.Json does not guarantee alpha order by default,
        // so we serialize a hand-ordered anonymous type.
        WriteIndented = false,
    };

    /// <summary>
    /// Returns a key for the full search result:
    ///   search:{collection}:{sha256(normalizedQuery|topK|useReranker|searchMode)}
    /// </summary>
    public static string SearchKey(
        string query,
        string collection,
        int topK,
        bool useReranker,
        string searchMode)
    {
        // Normalize: lowercase, collapse internal whitespace, trim edges.
        var normalizedQuery = NormalizeText(query);
        var normalizedCollection = collection.Trim().ToLowerInvariant();
        var normalizedMode = searchMode.Trim().ToLowerInvariant();

        // Canonical JSON with deterministic field order.
        var payload = JsonSerializer.Serialize(new
        {
            collection = normalizedCollection,
            mode = normalizedMode,
            query = normalizedQuery,
            reranker = useReranker,
            topK,
        }, CanonicalJson);

        var hash = Sha256Hex(payload);
        return $"search:{normalizedCollection}:{hash}";
    }

    /// <summary>
    /// Returns a key for a query embedding vector:
    ///   emb:{sha256(normalizedQuery)}
    /// Embeddings depend only on the query text — same text = same vector.
    /// </summary>
    public static string EmbeddingKey(string query)
    {
        var normalizedQuery = NormalizeText(query);
        var hash = Sha256Hex(normalizedQuery);
        return $"emb:{hash}";
    }

    /// <summary>
    /// Returns the Redis set key that tracks all search cache keys for a collection.
    /// Used to implement collection-scoped invalidation without SCAN.
    /// </summary>
    public static string CollectionIndexKey(string collection) =>
        $"search-index:{collection.Trim().ToLowerInvariant()}";

    // -------------------------------------------------------------------------

    private static string NormalizeText(string text) =>
        string.Join(' ', text.Trim().ToLowerInvariant()
            .Split(default(char[]), StringSplitOptions.RemoveEmptyEntries));

    private static string Sha256Hex(string input)
    {
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(input));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }
}
```

**Key format examples**:

| Input | Key |
|---|---|
| `"What is the refund policy?"`, collection=`documents`, topK=5, semantic | `search:documents:a3f9d2...` |
| `"  What is the REFUND policy?  "` (same query, different whitespace/case) | `search:documents:a3f9d2...` (identical) |
| Same query, `useReranker=true` | `search:documents:7bc1e4...` (different hash) |

---

## 6. Cache Abstraction Layer

Rather than scattering `IDistributedCache` calls across the codebase, all cache operations are centralized in a `SearchCacheService`. This isolates serialization logic and TTL policy in one place.

**File**: `OpenRAG.Api/Services/Cache/SearchCacheService.cs`

```csharp
using System.Text.Json;
using Microsoft.Extensions.Caching.Distributed;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Options;
using OpenRAG.Api.Configuration;
using OpenRAG.Api.Models.Dto.Responses;

namespace OpenRAG.Api.Services.Cache;

/// <summary>
/// Wraps IDistributedCache (Redis when configured, otherwise a no-op stub)
/// and IMemoryCache (in-process fallback) for search result and embedding caching.
/// </summary>
public class SearchCacheService(
    IDistributedCache? distributedCache,
    IMemoryCache memoryCache,
    IOptions<CacheOptions> options,
    ILogger<SearchCacheService> logger)
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly CacheOptions _opts = options.Value;

    // ── Search result cache ───────────────────────────────────────────────────

    public async Task<List<ChunkResult>?> GetSearchResultAsync(string cacheKey, CancellationToken ct = default)
    {
        // 1. Try distributed cache (Redis)
        if (distributedCache is not null)
        {
            try
            {
                var bytes = await distributedCache.GetAsync(cacheKey, ct);
                if (bytes is not null)
                {
                    var result = JsonSerializer.Deserialize<List<ChunkResult>>(bytes, JsonOpts);
                    logger.LogInformation(
                        "[Cache] HIT (Redis) key={Key} results={Count}",
                        cacheKey, result?.Count ?? 0);
                    return result;
                }
            }
            catch (Exception ex)
            {
                // Redis failure must not break the request — fall through to memory cache.
                logger.LogWarning(ex, "[Cache] Redis GET failed for key={Key}, falling back to memory", cacheKey);
            }
        }

        // 2. Try in-memory cache
        if (memoryCache.TryGetValue(cacheKey, out List<ChunkResult>? memResult))
        {
            logger.LogInformation(
                "[Cache] HIT (Memory) key={Key} results={Count}",
                cacheKey, memResult?.Count ?? 0);
            return memResult;
        }

        logger.LogInformation("[Cache] MISS key={Key}", cacheKey);
        return null;
    }

    public async Task SetSearchResultAsync(
        string cacheKey,
        string collection,
        List<ChunkResult> results,
        CancellationToken ct = default)
    {
        // 1. Write to in-memory cache (always, acts as L1)
        memoryCache.Set(cacheKey, results, _opts.SearchTtl);

        // 2. Write to Redis (L2) and register key in the collection index
        if (distributedCache is not null)
        {
            try
            {
                var bytes = JsonSerializer.SerializeToUtf8Bytes(results, JsonOpts);
                var distOpts = new DistributedCacheEntryOptions
                {
                    AbsoluteExpirationRelativeToNow = _opts.SearchTtl,
                };
                await distributedCache.SetAsync(cacheKey, bytes, distOpts, ct);

                // Track this key in the collection's invalidation index.
                // The index is itself a cached list of keys (JSON).
                await AddKeyToCollectionIndexAsync(cacheKey, collection, ct);

                logger.LogInformation(
                    "[Cache] SET key={Key} ttl={Ttl}s collection={Collection}",
                    cacheKey, _opts.SearchTtlSeconds, collection);
            }
            catch (Exception ex)
            {
                logger.LogWarning(ex, "[Cache] Redis SET failed for key={Key}", cacheKey);
            }
        }
    }

    // ── Embedding cache ───────────────────────────────────────────────────────

    public async Task<float[]?> GetEmbeddingAsync(string embKey, CancellationToken ct = default)
    {
        if (distributedCache is not null)
        {
            try
            {
                var bytes = await distributedCache.GetAsync(embKey, ct);
                if (bytes is not null)
                {
                    var emb = JsonSerializer.Deserialize<float[]>(bytes, JsonOpts);
                    logger.LogInformation("[Cache] HIT (Redis/Emb) key={Key}", embKey);
                    return emb;
                }
            }
            catch (Exception ex)
            {
                logger.LogWarning(ex, "[Cache] Redis GET (emb) failed for key={Key}", embKey);
            }
        }

        if (memoryCache.TryGetValue(embKey, out float[]? memEmb))
        {
            logger.LogInformation("[Cache] HIT (Memory/Emb) key={Key}", embKey);
            return memEmb;
        }

        return null;
    }

    public async Task SetEmbeddingAsync(string embKey, float[] embedding, CancellationToken ct = default)
    {
        memoryCache.Set(embKey, embedding, _opts.EmbeddingTtl);

        if (distributedCache is not null)
        {
            try
            {
                var bytes = JsonSerializer.SerializeToUtf8Bytes(embedding, JsonOpts);
                var distOpts = new DistributedCacheEntryOptions
                {
                    AbsoluteExpirationRelativeToNow = _opts.EmbeddingTtl,
                };
                await distributedCache.SetAsync(embKey, bytes, distOpts, ct);
            }
            catch (Exception ex)
            {
                logger.LogWarning(ex, "[Cache] Redis SET (emb) failed for key={Key}", embKey);
            }
        }
    }

    // ── Collection-scoped invalidation ────────────────────────────────────────

    /// <summary>
    /// Invalidates all cached search results for a given collection.
    /// Called by DocumentService after index or delete operations.
    ///
    /// Strategy: maintain a Redis list (JSON) of all search cache keys that
    /// belong to this collection. On invalidation, iterate and delete each key.
    ///
    /// IDistributedCache does not support pattern-based removal (SCAN + DEL),
    /// so this collection index is the portable alternative that works with
    /// any IDistributedCache backend (Redis, SQL Server, etc.).
    /// </summary>
    public async Task InvalidateCollectionAsync(string collection, CancellationToken ct = default)
    {
        var indexKey = CacheKeyFactory.CollectionIndexKey(collection);

        // Always clear in-memory entries by tag (in .NET 9 use cache tags;
        // for .NET 8 we must track manually — clear all memory cache entries
        // for this collection by removing tracked keys).
        if (memoryCache.TryGetValue(indexKey, out List<string>? trackedKeys) && trackedKeys is not null)
        {
            foreach (var key in trackedKeys)
                memoryCache.Remove(key);

            memoryCache.Remove(indexKey);
            logger.LogInformation(
                "[Cache] Invalidated {Count} memory entries for collection={Collection}",
                trackedKeys.Count, collection);
        }

        if (distributedCache is null) return;

        try
        {
            // Retrieve the index of all keys belonging to this collection.
            var indexBytes = await distributedCache.GetAsync(indexKey, ct);
            if (indexBytes is null)
            {
                logger.LogInformation("[Cache] No Redis index for collection={Collection}", collection);
                return;
            }

            var keys = JsonSerializer.Deserialize<List<string>>(indexBytes, JsonOpts) ?? [];

            // Delete each tracked search result key.
            var tasks = keys.Select(k => distributedCache.RemoveAsync(k, ct));
            await Task.WhenAll(tasks);

            // Remove the index itself.
            await distributedCache.RemoveAsync(indexKey, ct);

            logger.LogInformation(
                "[Cache] Invalidated {Count} Redis entries for collection={Collection}",
                keys.Count, collection);
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex,
                "[Cache] Failed to invalidate Redis cache for collection={Collection}", collection);
        }
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private async Task AddKeyToCollectionIndexAsync(
        string cacheKey,
        string collection,
        CancellationToken ct)
    {
        var indexKey = CacheKeyFactory.CollectionIndexKey(collection);

        // In-memory index tracking
        var memKeys = memoryCache.GetOrCreate(indexKey, e =>
        {
            e.SlidingExpiration = TimeSpan.FromDays(1);
            return new List<string>();
        })!;
        if (!memKeys.Contains(cacheKey)) memKeys.Add(cacheKey);

        // Redis index tracking
        if (distributedCache is null) return;
        try
        {
            List<string> keys = [];
            var existing = await distributedCache.GetAsync(indexKey, ct);
            if (existing is not null)
                keys = JsonSerializer.Deserialize<List<string>>(existing, JsonOpts) ?? [];

            if (!keys.Contains(cacheKey))
            {
                keys.Add(cacheKey);
                var indexBytes = JsonSerializer.SerializeToUtf8Bytes(keys, JsonOpts);
                // Index TTL is longer than any individual entry — 48h.
                await distributedCache.SetAsync(indexKey, indexBytes,
                    new DistributedCacheEntryOptions
                    {
                        AbsoluteExpirationRelativeToNow = TimeSpan.FromHours(48),
                    }, ct);
            }
        }
        catch (Exception ex)
        {
            logger.LogWarning(ex, "[Cache] Failed to update collection index for collection={Collection}", collection);
        }
    }
}
```

---

## 7. Program.cs — Service Registration with Redis/Memory Fallback

**File**: `OpenRAG.Api/Program.cs` (complete updated version)

```csharp
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;
using OpenRAG.Api.Configuration;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Cache;
using OpenRAG.Api.Services.Chunking;

var builder = WebApplication.CreateBuilder(args);

// ── Cache configuration ───────────────────────────────────────────────────────
var cacheSection = builder.Configuration.GetSection(CacheOptions.SectionName);
builder.Services.Configure<CacheOptions>(cacheSection);
var cacheOpts = cacheSection.Get<CacheOptions>() ?? new CacheOptions();

// Always register in-memory cache as the L1 / standalone fallback.
builder.Services.AddMemoryCache();

// Register Redis (L2) only when a connection string is provided.
// When Redis is absent, IDistributedCache is not registered at all — the
// SearchCacheService handles a null IDistributedCache gracefully.
var redisConnectionString = cacheOpts.Redis.ConnectionString;
if (!string.IsNullOrWhiteSpace(redisConnectionString))
{
    builder.Services.AddStackExchangeRedisCache(o =>
    {
        o.Configuration = redisConnectionString;
        o.InstanceName = "openrag:";
    });
    builder.Logging.AddFilter("Microsoft.Extensions.Caching.StackExchangeRedis", LogLevel.Warning);
}
// Note: do NOT call AddDistributedMemoryCache() as a fallback here.
// We intentionally leave IDistributedCache unregistered when Redis is absent.
// SearchCacheService receives IDistributedCache? (nullable) and handles it.
// If you need IDistributedCache to always be resolvable (e.g., for third-party
// middleware), uncomment the next block instead:
// else { builder.Services.AddDistributedMemoryCache(); }

builder.Services.AddSingleton<SearchCacheService>();

// ── Database ──────────────────────────────────────────────────────────────────
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString("Default")
        ?? "Data Source=../data/openrag.db"));

// ── ML Service HTTP client ────────────────────────────────────────────────────
builder.Services.AddHttpClient<MlClient>(client =>
{
    client.BaseAddress = new Uri(builder.Configuration["MlService:BaseUrl"] ?? "http://localhost:8001");
    client.Timeout = TimeSpan.FromSeconds(300);
});

// ── LLM client (optional) ────────────────────────────────────────────────────
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

// ── Application services ──────────────────────────────────────────────────────
builder.Services.AddSingleton<MarkdownChunker>();
builder.Services.AddScoped<DocumentService>();
builder.Services.AddScoped<CollectionService>();
builder.Services.AddScoped<ChatService>();

// ── SignalR ───────────────────────────────────────────────────────────────────
builder.Services.AddSignalR();

// ── Controllers + CORS ───────────────────────────────────────────────────────
builder.Services.AddControllers();
builder.Services.AddCors(o => o.AddDefaultPolicy(p =>
    p.WithOrigins("http://localhost:5173", "http://localhost:8000")
     .AllowAnyMethod()
     .AllowAnyHeader()
     .AllowCredentials()));

var app = builder.Build();

// ── Migrate DB on startup ─────────────────────────────────────────────────────
using (var scope = app.Services.CreateScope())
{
    var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
    db.Database.Migrate();
}

app.UseCors();
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapControllers();
app.MapHub<ProgressHub>("/ws/progress");
app.MapFallbackToFile("index.html");

app.Run();
```

**Important note on nullable `IDistributedCache`**: ASP.NET Core's DI container will inject `null` for an unregistered optional service only when the constructor parameter is declared as `T?` (nullable reference type). The `SearchCacheService` constructor uses `IDistributedCache? distributedCache` — this is intentional.

---

## 8. SearchController.cs — Cache Bypass + Cache-Aware Search

**File**: `OpenRAG.Api/Controllers/SearchController.cs` (complete updated version)

```csharp
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Cache;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/search")]
public class SearchController(
    MlClient ml,
    LlmClient llm,
    SearchCacheService cache,
    ILogger<SearchController> logger) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Search(
        [FromBody] SearchRequest req,
        [FromQuery] bool noCache = false,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        List<ChunkResult> results;

        if (noCache)
        {
            logger.LogInformation("[Search] Cache bypass requested for query='{Query}'", req.Query);
            results = await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);
        }
        else
        {
            var cacheKey = CacheKeyFactory.SearchKey(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode);
            var cached = await cache.GetSearchResultAsync(cacheKey, ct);

            if (cached is not null)
            {
                results = cached;
                // Surface cache status to the client for debugging.
                Response.Headers["X-Cache"] = "HIT";
            }
            else
            {
                results = await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);
                // Fire-and-forget cache write — do not delay the response.
                _ = cache.SetSearchResultAsync(cacheKey, req.Collection, results, CancellationToken.None);
                Response.Headers["X-Cache"] = "MISS";
            }
        }

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            return Ok(new SearchResponse(req.Query, results, results.Count, generated?.Answer, generated?.Citations));
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }
}
```

**`?noCache=true` usage**:
```
POST /api/search?noCache=true
{ "query": "refund policy", "collection": "documents" }
```

The `X-Cache: HIT` / `X-Cache: MISS` response header lets developers inspect cache behavior without log access.

---

## 9. Embedding Cache — MlClient.cs Integration

The current `MlClient.SearchAsync` sends the entire search pipeline to the Python service in a single call. The embedding cache makes most sense either:

**Option A (Recommended)**: Cache at the .NET boundary — wrap `MlClient` calls in a cache-aware service before results are sent to `SearchController`. Since the ML service currently bundles embed + search atomically, this means caching the full result (already done above). The embedding cache described below is relevant when the ML service is split into separate `/embed` and `/search` endpoints in the future.

**Option B**: Add a dedicated `/ml/embed` endpoint to the Python service and cache the resulting vector in .NET. This is the most impactful approach if the same query is searched with different `topK` or `searchMode` values.

For Option B — inject `SearchCacheService` into `MlClient`:

**File**: `OpenRAG.Api/Services/MlClient.cs` (embedding cache extension)

```csharp
using System.Net.Http.Json;
using System.Text.Json;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services.Cache;

namespace OpenRAG.Api.Services;

public record MlChunkInput(string Text, Dictionary<string, string> Metadata);
public record MlIndexRequest(string DocumentId, string Collection, List<MlChunkInput> Chunks);
public record MlIndexResponse(string DocumentId, int ChunkCount, bool Ok);
public record MlSearchRequest(string Query, string Collection, int TopK = 5, bool UseReranker = false, string SearchMode = "semantic");
public record MlEmbedRequest(string Query);
public record MlEmbedResponse(float[] Embedding);
public record MlDeleteDocRequest(string DocumentId, string Collection);
public record MlDeleteDocResponse(int ChunksDeleted, bool Ok);
public record MlCollectionRequest(string Name);
public record MlHealthResponse(bool Ok, string Model, string Device);

public class MlClient(
    HttpClient http,
    SearchCacheService cache,
    ILogger<MlClient> logger)
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    // ── Embedding with cache ──────────────────────────────────────────────────

    /// <summary>
    /// Embeds a query, checking the embedding cache first.
    /// A 24-hour TTL is appropriate: embeddings are deterministic for fixed
    /// model weights, so the vector for "refund policy" never changes unless
    /// the embedding model is updated.
    ///
    /// Requires a new /ml/embed endpoint on the Python ML service.
    /// </summary>
    public async Task<float[]> EmbedQueryAsync(string query, CancellationToken ct = default)
    {
        var embKey = CacheKeyFactory.EmbeddingKey(query);
        var cached = await cache.GetEmbeddingAsync(embKey, ct);
        if (cached is not null)
        {
            logger.LogDebug("[MlClient] Embedding cache HIT for key={Key}", embKey);
            return cached;
        }

        var response = await http.PostAsJsonAsync("/ml/embed", new MlEmbedRequest(query), JsonOpts, ct);
        response.EnsureSuccessStatusCode();
        var result = await response.Content.ReadFromJsonAsync<MlEmbedResponse>(JsonOpts, ct)
            ?? throw new InvalidOperationException("Empty embed response");

        // Cache asynchronously — do not block the request path.
        _ = cache.SetEmbeddingAsync(embKey, result.Embedding, CancellationToken.None);

        return result.Embedding;
    }

    // ── Existing methods (unchanged) ──────────────────────────────────────────

    public async Task<string> ConvertFileAsync(Stream fileStream, string filename, CancellationToken ct = default)
    {
        using var content = new MultipartFormDataContent();
        content.Add(new StreamContent(fileStream), "file", filename);
        content.Add(new StringContent(filename), "filename");
        var response = await http.PostAsync("/ml/convert", content, ct);
        response.EnsureSuccessStatusCode();
        var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        return result.GetProperty("markdown").GetString() ?? "";
    }

    public async Task<MlIndexResponse> IndexChunksAsync(MlIndexRequest req, CancellationToken ct = default)
    {
        var response = await http.PostAsJsonAsync("/ml/index", req, JsonOpts, ct);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<MlIndexResponse>(JsonOpts, ct)
               ?? throw new InvalidOperationException("Empty response from ML service");
    }

    public async Task<List<ChunkResult>> SearchAsync(
        string query, string collection, int topK,
        bool useReranker = false, string searchMode = "semantic",
        CancellationToken ct = default)
    {
        var req = new MlSearchRequest(query, collection, topK, useReranker, searchMode);
        var response = await http.PostAsJsonAsync("/ml/search", req, JsonOpts, ct);
        response.EnsureSuccessStatusCode();

        var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        var results = new List<ChunkResult>();

        foreach (var item in result.GetProperty("results").EnumerateArray())
        {
            var text = item.GetProperty("text").GetString() ?? "";
            var score = item.GetProperty("score").GetDouble();
            double? rerankScore = null;
            if (item.TryGetProperty("rerank_score", out var rs) && rs.ValueKind != JsonValueKind.Null)
                rerankScore = rs.GetDouble();
            var meta = new Dictionary<string, object>();
            foreach (var prop in item.GetProperty("metadata").EnumerateObject())
                meta[prop.Name] = prop.Value.ToString();
            results.Add(new ChunkResult(text, score, meta, rerankScore));
        }

        return results;
    }

    public async Task<int> DeleteDocumentAsync(Guid documentId, string collection, CancellationToken ct = default)
    {
        var response = await http.PostAsJsonAsync("/ml/documents/delete",
            new MlDeleteDocRequest(documentId.ToString(), collection), JsonOpts, ct);
        response.EnsureSuccessStatusCode();
        var result = await response.Content.ReadFromJsonAsync<MlDeleteDocResponse>(JsonOpts, ct);
        return result?.ChunksDeleted ?? 0;
    }

    public async Task EnsureCollectionAsync(string name, CancellationToken ct = default)
    {
        var response = await http.PostAsJsonAsync("/ml/collections/ensure", new MlCollectionRequest(name), JsonOpts, ct);
        response.EnsureSuccessStatusCode();
    }

    public async Task DeleteCollectionAsync(string name, CancellationToken ct = default)
    {
        var response = await http.PostAsJsonAsync("/ml/collections/delete", new MlCollectionRequest(name), JsonOpts, ct);
        response.EnsureSuccessStatusCode();
    }

    public async Task<bool> HealthAsync(CancellationToken ct = default)
    {
        try
        {
            var response = await http.GetAsync("/ml/health", ct);
            return response.IsSuccessStatusCode;
        }
        catch (Exception ex)
        {
            logger.LogWarning("ML service health check failed: {Message}", ex.Message);
            return false;
        }
    }
}
```

---

## 10. Cache Invalidation — DocumentService.cs

**File**: `OpenRAG.Api/Services/DocumentService.cs` (complete updated version)

Cache invalidation is called after every successful index or delete operation. The collection-level granularity is intentional: it is conservative (may evict still-valid results) but correct. Fine-grained document-level invalidation is not feasible because a new document changes the ranking of all queries against its collection.

```csharp
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Models.Dto.Events;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services.Cache;
using OpenRAG.Api.Services.Chunking;

namespace OpenRAG.Api.Services;

public class DocumentService(
    AppDbContext db,
    MlClient ml,
    MarkdownChunker chunker,
    IHubContext<ProgressHub> hub,
    SearchCacheService cache,
    ILogger<DocumentService> logger)
{
    private void ReportProgress(string documentId, string stage, int progress) =>
        hub.Clients.All.SendAsync("progress",
            new ProgressEvent("progress", documentId, stage, progress))
            .ContinueWith(
                t => logger.LogWarning(t.Exception, "Failed to send progress event for {DocumentId}", documentId),
                TaskContinuationOptions.OnlyOnFaulted);

    public async Task<IngestResponse> IngestFileAsync(
        Stream fileStream, string filename, string collection, long sizeBytes, CancellationToken ct = default)
    {
        var col = await GetOrCreateCollectionAsync(collection, ct);
        var documentId = Guid.NewGuid();
        var docIdStr = documentId.ToString();

        var doc = new Document
        {
            Id = documentId,
            Filename = filename,
            CollectionId = col.Id,
            SizeBytes = sizeBytes,
            Status = "indexing",
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            ReportProgress(docIdStr, "converting", 10);
            var markdown = await ml.ConvertFileAsync(fileStream, filename, ct);

            ReportProgress(docIdStr, "chunking", 35);
            var chunks = chunker.Chunk(markdown, new Dictionary<string, string> { ["filename"] = filename });
            logger.LogInformation("Chunked '{Filename}' into {Count} chunks", filename, chunks.Count);

            if (chunks.Count == 0)
            {
                doc.Status = "indexed";
                doc.ChunkCount = 0;
                doc.IndexedAt = DateTime.UtcNow;
                await db.SaveChangesAsync(ct);
                ReportProgress(docIdStr, "done", 100);
                return new IngestResponse(docIdStr, filename, 0, "No content extracted");
            }

            ReportProgress(docIdStr, "embedding", 55);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "done", 100);

            // Invalidate cached search results for this collection.
            // New document content changes rankings — stale results must be evicted.
            await cache.InvalidateCollectionAsync(collection, ct);
            logger.LogInformation(
                "[Cache] Invalidated collection={Collection} after indexing '{Filename}'",
                collection, filename);

            return new IngestResponse(docIdStr, filename, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest '{Filename}'", filename);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "failed", 0);
            throw;
        }
    }

    public async Task<IngestResponse> IngestTextAsync(
        string text, string title, string collection, CancellationToken ct = default)
    {
        var col = await GetOrCreateCollectionAsync(collection, ct);
        var documentId = Guid.NewGuid();
        var docIdStr = documentId.ToString();

        ReportProgress(docIdStr, "chunking", 35);
        var chunks = chunker.Chunk(text, new Dictionary<string, string> { ["filename"] = title });

        var doc = new Document
        {
            Id = documentId,
            Filename = title,
            CollectionId = col.Id,
            SizeBytes = System.Text.Encoding.UTF8.GetByteCount(text),
            Status = "indexing",
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            if (chunks.Count == 0)
            {
                doc.Status = "indexed";
                doc.ChunkCount = 0;
                doc.IndexedAt = DateTime.UtcNow;
                await db.SaveChangesAsync(ct);
                ReportProgress(docIdStr, "done", 100);
                return new IngestResponse(docIdStr, title, 0, "No content to index");
            }

            ReportProgress(docIdStr, "embedding", 55);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "done", 100);

            // Invalidate cached search results for this collection.
            await cache.InvalidateCollectionAsync(collection, ct);
            logger.LogInformation(
                "[Cache] Invalidated collection={Collection} after ingesting text '{Title}'",
                collection, title);

            return new IngestResponse(docIdStr, title, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest text '{Title}'", title);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "failed", 0);
            throw;
        }
    }

    public async Task<DocumentListResponse> ListDocumentsAsync(string collection, CancellationToken ct = default)
    {
        var docs = await db.Documents
            .Include(d => d.Collection)
            .Where(d => d.Collection.Name == collection && d.Status == "indexed")
            .OrderByDescending(d => d.CreatedAt)
            .Select(d => new DocumentInfo(
                d.Id.ToString(),
                d.Filename,
                d.Collection.Name,
                d.ChunkCount,
                d.CreatedAt.ToString("o")))
            .ToListAsync(ct);

        return new DocumentListResponse(docs, docs.Count);
    }

    public async Task<StatusResponse> DeleteDocumentAsync(Guid documentId, string collection, CancellationToken ct = default)
    {
        var doc = await db.Documents
            .Include(d => d.Collection)
            .FirstOrDefaultAsync(d => d.Id == documentId && d.Collection.Name == collection, ct);

        if (doc is null)
            return new StatusResponse("error", "Document not found");

        var deleted = await ml.DeleteDocumentAsync(documentId, collection, ct);
        db.Documents.Remove(doc);
        await db.SaveChangesAsync(ct);

        // Invalidate cached search results for this collection.
        await cache.InvalidateCollectionAsync(collection, ct);
        logger.LogInformation(
            "[Cache] Invalidated collection={Collection} after deleting document={DocumentId}",
            collection, documentId);

        return new StatusResponse("ok", $"Deleted {deleted} chunks");
    }

    private async Task<Collection> GetOrCreateCollectionAsync(string name, CancellationToken ct)
    {
        var col = await db.Collections.FirstOrDefaultAsync(c => c.Name == name, ct);
        if (col is not null) return col;

        col = new Collection { Name = name };
        db.Collections.Add(col);
        await db.SaveChangesAsync(ct);
        await ml.EnsureCollectionAsync(name, ct);
        return col;
    }
}
```

---

## 11. TTL Strategy

| Cache type | Key prefix | TTL | Rationale |
|---|---|---|---|
| Search results | `search:{collection}:{hash}` | **5 minutes** | Content can change on index/delete; short TTL limits staleness |
| Query embeddings | `emb:{hash}` | **24 hours** | Deterministic for fixed model weights; changes only on model upgrade |
| Collection key index | `search-index:{collection}` | **48 hours** | Must outlive the search results it tracks |
| Collection metadata | `meta:{collection}:{hash}` | **1 minute** | Rapid refresh; applies to listing/count endpoints |

TTL is fully configurable via `appsettings.json` (see section 4). The values above are defaults baked into `CacheOptions`.

**On embedding model upgrade**: when the embedding model is swapped, all 24-hour embedding cache entries become stale. Mitigate this by:
1. Flushing the Redis cache with `redis-cli FLUSHDB` during the upgrade rollout, or
2. Including the model name in the embedding cache key: `emb:{modelName}:{hash}`.

Option 2 is more robust. The model name can be fetched from the `/ml/health` endpoint at startup and stored as a singleton configuration value.

---

## 12. Metrics and Logging

The `SearchCacheService` already emits structured log messages at `Information` level for every HIT/MISS/SET/invalidation event. These are sufficient for development and production monitoring via a log aggregator (Seq, Datadog, etc.).

For a richer metrics story, add OpenTelemetry counters as shown below. These slots can be wired to a future `builder.Services.AddOpenTelemetry()` call without changing any other code.

**File**: `OpenRAG.Api/Services/Cache/CacheMetrics.cs`

```csharp
using System.Diagnostics.Metrics;

namespace OpenRAG.Api.Services.Cache;

/// <summary>
/// Instruments cache activity for OpenTelemetry or any IMeterFactory consumer.
/// Register as a singleton. Metrics are no-ops until an IMeterListener is attached.
/// </summary>
public sealed class CacheMetrics : IDisposable
{
    private readonly Meter _meter;

    public readonly Counter<long> SearchHits;
    public readonly Counter<long> SearchMisses;
    public readonly Counter<long> EmbeddingHits;
    public readonly Counter<long> EmbeddingMisses;
    public readonly Counter<long> Invalidations;

    public CacheMetrics(IMeterFactory meterFactory)
    {
        _meter = meterFactory.Create("OpenRAG.Cache");
        SearchHits    = _meter.CreateCounter<long>("cache.search.hits",    description: "Search result cache hits");
        SearchMisses  = _meter.CreateCounter<long>("cache.search.misses",  description: "Search result cache misses");
        EmbeddingHits = _meter.CreateCounter<long>("cache.embedding.hits", description: "Embedding cache hits");
        EmbeddingMisses = _meter.CreateCounter<long>("cache.embedding.misses", description: "Embedding cache misses");
        Invalidations = _meter.CreateCounter<long>("cache.invalidations",  description: "Collection cache invalidations");
    }

    public void Dispose() => _meter.Dispose();
}
```

Register in `Program.cs`:

```csharp
builder.Services.AddSingleton<CacheMetrics>();
```

Then in `SearchCacheService`, inject `CacheMetrics` and call `metrics.SearchHits.Add(1)` / `metrics.SearchMisses.Add(1)` instead of (or in addition to) the `ILogger` calls. Until an OpenTelemetry exporter is wired up, the counters record into an in-process listener with zero overhead.

**Sample log output** (structured JSON via `appsettings.json` + Serilog/default provider):

```
[INF] [Cache] MISS key=search:documents:a3f9d2c1...
[INF] [Cache] SET key=search:documents:a3f9d2c1... ttl=300s collection=documents
[INF] [Cache] HIT (Redis) key=search:documents:a3f9d2c1... results=5
[INF] [Cache] Invalidated 12 Redis entries for collection=documents
```

**Hit rate calculation** (can be computed from log aggregation):

```
hit_rate = SearchHits / (SearchHits + SearchMisses)
```

---

## 13. Docker Compose

No `docker-compose.yml` exists in the repository. The following file creates a complete local development stack including the Redis service.

**File**: `docker-compose.yml` (project root: `d:/Works/trgiangvp3/open-rag/`)

```yaml
version: "3.9"

services:
  # ── Redis cache ─────────────────────────────────────────────────────────────
  redis:
    image: redis:7.2-alpine
    container_name: openrag-redis
    ports:
      - "6379:6379"
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ── ML Service (Python / FastAPI) ───────────────────────────────────────────
  ml-service:
    build:
      context: ./ml_service
      dockerfile: Dockerfile
    container_name: openrag-ml
    ports:
      - "8001:8001"
    volumes:
      - chroma_data:/app/chroma_db
    environment:
      - ML_HOST=0.0.0.0
      - ML_PORT=8001
    depends_on:
      redis:
        condition: service_healthy

  # ── .NET API ─────────────────────────────────────────────────────────────────
  api:
    build:
      context: ./OpenRAG.Api
      dockerfile: Dockerfile
    container_name: openrag-api
    ports:
      - "8000:8000"
    volumes:
      - sqlite_data:/data
    environment:
      - ConnectionStrings__Default=Data Source=/data/openrag.db
      - MlService__BaseUrl=http://ml-service:8001
      - Cache__Redis__ConnectionString=redis:6379,abortConnect=false
      - Cache__SearchTtlSeconds=300
      - Cache__EmbeddingTtlSeconds=86400
    depends_on:
      redis:
        condition: service_healthy
      ml-service:
        condition: service_started

  # ── Vue frontend (dev mode with Vite) ────────────────────────────────────────
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: openrag-frontend
    ports:
      - "5173:5173"
    depends_on:
      - api

volumes:
  redis_data:
  chroma_data:
  sqlite_data:
```

**Running locally without Docker**: leave `Cache:Redis:ConnectionString` empty in `appsettings.json` — the system degrades gracefully to in-process memory cache.

---

## 14. Trade-offs

### 14.1 Consistency vs Performance

| Trade-off | Impact | Mitigation |
|---|---|---|
| Stale search results during 5-minute TTL | Users may not see newly indexed docs immediately | Explicitly invalidate on index/delete (already designed above) |
| Memory cache L1 is per-instance | In a multi-instance deployment, each pod has a cold memory cache | Redis (L2) provides the shared, warm cache across all instances |
| Collection-level invalidation is over-broad | Adding one doc invalidates all queries for that collection | Acceptable; document indexing is infrequent compared to search |
| Embedding model upgrade makes emb cache stale | Wrong vectors returned for 24h | Flush Redis on deploy, or include model version in key |
| High-cardinality queries never cache | Each unique query is a cache miss | Expected; TTL cleanup prevents unbounded growth |

### 14.2 Memory Sizing

With a 5-minute TTL and a typical `List<ChunkResult>` of 5 items at ~2 KB JSON each (~10 KB per entry):

- 1,000 unique queries/day → ~10 MB Redis memory
- 10,000 unique queries/day → ~100 MB Redis memory

Redis `maxmemory-policy allkeys-lru` is recommended so Redis self-evicts LRU entries under memory pressure without crashing.

Add to `docker-compose.yml` Redis command:
```yaml
command: redis-server --save 60 1 --loglevel warning --maxmemory 256mb --maxmemory-policy allkeys-lru
```

### 14.3 In-Memory Cache vs Redis (When to Use Each)

| Scenario | Recommendation |
|---|---|
| Single-process development / testing | Memory cache only (Redis not needed) |
| Single-process production (low traffic) | Memory cache only (simpler ops) |
| Multi-instance production (load balanced) | Redis required for cross-instance cache sharing |
| High availability required | Redis Sentinel or Redis Cluster |

### 14.4 Alternative: Cache in the Python ML Service

The ML service (`ml_service/main_ml.py`) could also cache results internally using `functools.lru_cache` or Redis. This would be transparent to the .NET layer. However:

- Python-side caching does not benefit from the L1 memory cache in .NET.
- Python-side invalidation on document changes requires an additional API call or message queue event.
- The .NET layer has better observability integration (structured logs, metrics).

The .NET-side cache designed here is preferred.

### 14.5 The Collection Index Approach vs Redis SCAN

`IDistributedCache` does not expose `SCAN` or `KEYS` commands. Two approaches exist:

| Approach | Pros | Cons |
|---|---|---|
| **Collection index list** (designed here) | Works with any `IDistributedCache` backend | Slightly more complex; index can grow unbounded if not pruned |
| **StackExchange.Redis directly** (bypass `IDistributedCache`) | Full `SCAN + DEL` support | Couples the code to Redis; breaks if the backing store changes |

The collection index approach is chosen for portability. For large-scale deployments with thousands of unique queries per collection, add a periodic cleanup job that prunes expired keys from the index.

---

## 15. Implementation Checklist

- [ ] Add NuGet packages: `Microsoft.Extensions.Caching.StackExchangeRedis`, `Microsoft.Extensions.Caching.Memory`
- [ ] Create `OpenRAG.Api/Configuration/CacheOptions.cs`
- [ ] Create `OpenRAG.Api/Services/Cache/CacheKeyFactory.cs`
- [ ] Create `OpenRAG.Api/Services/Cache/SearchCacheService.cs`
- [ ] Create `OpenRAG.Api/Services/Cache/CacheMetrics.cs`
- [ ] Update `OpenRAG.Api/Program.cs` with cache service registration
- [ ] Update `OpenRAG.Api/Controllers/SearchController.cs` with cache + `?noCache` bypass
- [ ] Update `OpenRAG.Api/Services/DocumentService.cs` with invalidation calls
- [ ] Update `OpenRAG.Api/Services/MlClient.cs` with optional embedding cache (requires `/ml/embed` on ML service)
- [ ] Update `OpenRAG.Api/appsettings.json` with `Cache` section
- [ ] Create `docker-compose.yml` at repo root
- [ ] Add `--maxmemory` and `allkeys-lru` to Redis in docker-compose
- [ ] (Optional) Add `/ml/embed` endpoint to `ml_service/main_ml.py`
- [ ] (Optional) Wire `CacheMetrics` to OpenTelemetry exporter in `Program.cs`
