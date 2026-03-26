# OpenRAG: Lưu Cache Kết Quả Tìm Kiếm — Tài Liệu Thiết Kế

**Ngày**: 2026-03-26
**Trạng thái**: Đề Xuất Thiết Kế
**Mục tiêu**: `OpenRAG.Api` (.NET 8, ASP.NET Core)

---

## 1. Phát Biểu Vấn Đề

Mỗi lần gọi `POST /api/search` đều kích hoạt một chuỗi các thao tác ML tốn kém:

| Bước | Thành phần | Độ trễ |
|---|---|---|
| Query embedding | GPU inference (sentence-transformers) | 100–500 ms |
| ChromaDB vector search | Disk/memory ANN index | 50–200 ms |
| BM25 hybrid search (tùy chọn) | CPU, inverted index | 10–100 ms |
| Cross-encoder reranking (tùy chọn) | GPU inference | 200–800 ms |
| **Tổng cộng** | | **360 ms – 1.6 s** |

Các truy vấn giống hệt nhau được lặp lại sẽ thực thi lại toàn bộ pipeline. Trong một ứng dụng RAG điển hình, cùng một câu hỏi được hỏi thường xuyên (ví dụ: "What is the refund policy?"), đặc biệt trong môi trường nhiều người dùng. Điều này lãng phí chu kỳ GPU và làm tăng độ trễ phản hồi một cách không cần thiết.

`ChatService.cs` cũng gọi `ml.SearchAsync()` cho mỗi lượt chat, làm trầm trọng thêm vấn đề.

### Tốc Độ Cải Thiện Dự Kiến

| Kịch bản | Không có cache | Có cache (cache hit) |
|---|---|---|
| Warm semantic search | ~350 ms | ~2 ms (memory) / ~5 ms (Redis) |
| Warm reranker search | ~1.2 s | ~2 ms / ~5 ms |
| Cache miss (lần gọi đầu tiên) | ~1.2 s | ~1.2 s + ~1 ms chi phí ghi |
| Chỉ tái sử dụng embedding | ~700 ms | ~300 ms (bỏ qua bước embed) |

**Mục tiêu**: >80% các truy vấn lặp lại được phục vụ từ cache trong <10 ms.

---

## 2. Kiến Trúc Giải Pháp

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

## 3. Các Gói NuGet

Thêm vào `OpenRAG.Api/OpenRAG.Api.csproj`:

```xml
<PackageReference Include="Microsoft.Extensions.Caching.StackExchangeRedis" Version="8.*" />
<PackageReference Include="Microsoft.Extensions.Caching.Memory" Version="8.*" />
```

Không cần thêm bất kỳ thư viện bên thứ ba nào khác. `IDistributedCache` là abstraction được sử dụng xuyên suốt; `IMemoryCache` được dùng làm fallback cục bộ.

---

## 4. Cấu Hình

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

Khi `Cache:Redis:ConnectionString` để trống hoặc không được khai báo, hệ thống sẽ tự động chuyển sang sử dụng `IMemoryCache` trong tiến trình. Không cần Redis server cho môi trường phát triển cục bộ.

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

## 5. Chiến Lược Cache Key

**File**: `OpenRAG.Api/Services/Cache/CacheKeyFactory.cs`

Cache key phải ổn định bất kể sự khác biệt về khoảng trắng trong truy vấn hay thứ tự các trường JSON. Một hàm băm SHA-256 của đối tượng JSON đã được chuẩn hóa được dùng làm phần hậu tố của key. Điều này tạo ra một chuỗi hex có độ dài cố định 64 ký tự bất kể độ dài truy vấn.

```csharp
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace OpenRAG.Api.Services.Cache;

/// <summary>
/// Tạo ra các cache key xác định và chống xung đột cho các tham số tìm kiếm
/// và embedding vector.
/// </summary>
public static class CacheKeyFactory
{
    private static readonly JsonSerializerOptions CanonicalJson = new()
    {
        // Sắp xếp thuộc tính theo thứ tự bảng chữ cái đảm bảo độc lập với thứ tự các trường.
        // System.Text.Json không đảm bảo thứ tự alpha theo mặc định,
        // vì vậy ta serialize một anonymous type đã sắp xếp thủ công.
        WriteIndented = false,
    };

    /// <summary>
    /// Trả về key cho kết quả tìm kiếm đầy đủ:
    ///   search:{collection}:{sha256(normalizedQuery|topK|useReranker|searchMode)}
    /// </summary>
    public static string SearchKey(
        string query,
        string collection,
        int topK,
        bool useReranker,
        string searchMode)
    {
        // Chuẩn hóa: chuyển thành chữ thường, thu gọn khoảng trắng nội bộ, cắt hai đầu.
        var normalizedQuery = NormalizeText(query);
        var normalizedCollection = collection.Trim().ToLowerInvariant();
        var normalizedMode = searchMode.Trim().ToLowerInvariant();

        // JSON chuẩn hóa với thứ tự trường xác định.
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
    /// Trả về key cho embedding vector của một truy vấn:
    ///   emb:{sha256(normalizedQuery)}
    /// Embedding chỉ phụ thuộc vào văn bản truy vấn — cùng văn bản = cùng vector.
    /// </summary>
    public static string EmbeddingKey(string query)
    {
        var normalizedQuery = NormalizeText(query);
        var hash = Sha256Hex(normalizedQuery);
        return $"emb:{hash}";
    }

    /// <summary>
    /// Trả về Redis set key để theo dõi tất cả các search cache key của một collection.
    /// Được dùng để thực hiện invalidation theo phạm vi collection mà không cần SCAN.
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

**Ví dụ định dạng key**:

| Đầu vào | Key |
|---|---|
| `"What is the refund policy?"`, collection=`documents`, topK=5, semantic | `search:documents:a3f9d2...` |
| `"  What is the REFUND policy?  "` (cùng truy vấn, khác khoảng trắng/hoa thường) | `search:documents:a3f9d2...` (giống hệt) |
| Cùng truy vấn, `useReranker=true` | `search:documents:7bc1e4...` (hash khác) |

---

## 6. Lớp Trừu Tượng Cache

Thay vì phân tán các lời gọi `IDistributedCache` trên toàn bộ codebase, tất cả các thao tác cache được tập trung trong một `SearchCacheService`. Điều này cô lập logic serialization và chính sách TTL ở một nơi duy nhất.

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
/// Bọc IDistributedCache (Redis khi được cấu hình, ngược lại là no-op stub)
/// và IMemoryCache (fallback trong tiến trình) để cache kết quả tìm kiếm và embedding.
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

    // ── Cache kết quả tìm kiếm ───────────────────────────────────────────────────

    public async Task<List<ChunkResult>?> GetSearchResultAsync(string cacheKey, CancellationToken ct = default)
    {
        // 1. Thử distributed cache (Redis)
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
                // Lỗi Redis không được làm hỏng request — tiếp tục xuống memory cache.
                logger.LogWarning(ex, "[Cache] Redis GET failed for key={Key}, falling back to memory", cacheKey);
            }
        }

        // 2. Thử in-memory cache
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
        // 1. Ghi vào in-memory cache (luôn luôn, đóng vai trò L1)
        memoryCache.Set(cacheKey, results, _opts.SearchTtl);

        // 2. Ghi vào Redis (L2) và đăng ký key trong collection index
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

                // Theo dõi key này trong invalidation index của collection.
                // Index là một danh sách key được cache (JSON).
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

    // ── Cache embedding ───────────────────────────────────────────────────────

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

    // ── Invalidation theo phạm vi collection ────────────────────────────────────────────

    /// <summary>
    /// Xóa toàn bộ kết quả tìm kiếm đã cache cho một collection nhất định.
    /// Được gọi bởi DocumentService sau các thao tác index hoặc delete.
    ///
    /// Chiến lược: duy trì một Redis list (JSON) chứa tất cả search cache key
    /// thuộc về collection này. Khi invalidate, duyệt và xóa từng key.
    ///
    /// IDistributedCache không hỗ trợ xóa theo pattern (SCAN + DEL),
    /// nên collection index này là phương án thay thế khả chuyển hoạt động với
    /// mọi backend IDistributedCache (Redis, SQL Server, v.v.).
    /// </summary>
    public async Task InvalidateCollectionAsync(string collection, CancellationToken ct = default)
    {
        var indexKey = CacheKeyFactory.CollectionIndexKey(collection);

        // Luôn xóa các entry in-memory theo tag (trong .NET 9 dùng cache tags;
        // với .NET 8 phải theo dõi thủ công — xóa tất cả entry memory cache
        // của collection này bằng cách xóa các key đã theo dõi).
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
            // Lấy index chứa tất cả key thuộc collection này.
            var indexBytes = await distributedCache.GetAsync(indexKey, ct);
            if (indexBytes is null)
            {
                logger.LogInformation("[Cache] No Redis index for collection={Collection}", collection);
                return;
            }

            var keys = JsonSerializer.Deserialize<List<string>>(indexBytes, JsonOpts) ?? [];

            // Xóa từng search result key đã được theo dõi.
            var tasks = keys.Select(k => distributedCache.RemoveAsync(k, ct));
            await Task.WhenAll(tasks);

            // Xóa chính index.
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

    // ── Các hàm hỗ trợ nội bộ ───────────────────────────────────────────────────────

    private async Task AddKeyToCollectionIndexAsync(
        string cacheKey,
        string collection,
        CancellationToken ct)
    {
        var indexKey = CacheKeyFactory.CollectionIndexKey(collection);

        // Theo dõi index trong memory
        var memKeys = memoryCache.GetOrCreate(indexKey, e =>
        {
            e.SlidingExpiration = TimeSpan.FromDays(1);
            return new List<string>();
        })!;
        if (!memKeys.Contains(cacheKey)) memKeys.Add(cacheKey);

        // Theo dõi index trong Redis
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
                // TTL của index dài hơn bất kỳ entry riêng lẻ nào — 48h.
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

## 7. Program.cs — Đăng Ký Service với Redis/Memory Fallback

**File**: `OpenRAG.Api/Program.cs` (phiên bản cập nhật đầy đủ)

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

// ── Cấu hình cache ───────────────────────────────────────────────────────────
var cacheSection = builder.Configuration.GetSection(CacheOptions.SectionName);
builder.Services.Configure<CacheOptions>(cacheSection);
var cacheOpts = cacheSection.Get<CacheOptions>() ?? new CacheOptions();

// Luôn đăng ký in-memory cache làm L1 / standalone fallback.
builder.Services.AddMemoryCache();

// Chỉ đăng ký Redis (L2) khi có connection string.
// Khi Redis vắng mặt, IDistributedCache không được đăng ký —
// SearchCacheService xử lý IDistributedCache null một cách graceful.
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
// Lưu ý: KHÔNG gọi AddDistributedMemoryCache() làm fallback ở đây.
// Chúng ta cố ý để IDistributedCache không đăng ký khi Redis vắng mặt.
// SearchCacheService nhận IDistributedCache? (nullable) và xử lý nó.
// Nếu bạn cần IDistributedCache luôn có thể resolve được (ví dụ: cho middleware bên thứ ba),
// hãy bỏ comment khối tiếp theo thay thế:
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

// ── LLM client (tùy chọn) ────────────────────────────────────────────────────
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

// ── Migrate DB khi khởi động ─────────────────────────────────────────────────
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

**Lưu ý quan trọng về `IDistributedCache` nullable**: Container DI của ASP.NET Core sẽ inject `null` cho một service tùy chọn chưa được đăng ký chỉ khi tham số constructor được khai báo là `T?` (nullable reference type). Constructor của `SearchCacheService` dùng `IDistributedCache? distributedCache` — điều này là có chủ đích.

---

## 8. SearchController.cs — Cache Bypass + Tìm Kiếm Với Cache

**File**: `OpenRAG.Api/Controllers/SearchController.cs` (phiên bản cập nhật đầy đủ)

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
                // Hiển thị trạng thái cache cho client để debug.
                Response.Headers["X-Cache"] = "HIT";
            }
            else
            {
                results = await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);
                // Ghi cache theo kiểu fire-and-forget — không trì hoãn phản hồi.
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

**Cách dùng `?noCache=true`**:
```
POST /api/search?noCache=true
{ "query": "refund policy", "collection": "documents" }
```

Response header `X-Cache: HIT` / `X-Cache: MISS` cho phép developer kiểm tra hành vi cache mà không cần truy cập log.

---

## 9. Cache Embedding — Tích Hợp MlClient.cs

`MlClient.SearchAsync` hiện tại gửi toàn bộ pipeline tìm kiếm đến Python service trong một lần gọi duy nhất. Cache embedding có ý nghĩa nhất trong một trong hai trường hợp:

**Option A (Khuyến nghị)**: Cache tại ranh giới .NET — bọc các lời gọi `MlClient` trong một service có nhận thức về cache trước khi kết quả được gửi đến `SearchController`. Vì ML service hiện tại gộp embed + search vào một khối nguyên tử, điều này có nghĩa là cache toàn bộ kết quả (đã thực hiện ở trên). Cache embedding mô tả bên dưới sẽ phù hợp khi ML service được tách thành các endpoint `/embed` và `/search` riêng biệt trong tương lai.

**Option B**: Thêm một endpoint `/ml/embed` chuyên dụng vào Python service và cache embedding vector kết quả trong .NET. Đây là phương án có tác động lớn nhất nếu cùng một truy vấn được tìm kiếm với các giá trị `topK` hoặc `searchMode` khác nhau.

Với Option B — inject `SearchCacheService` vào `MlClient`:

**File**: `OpenRAG.Api/Services/MlClient.cs` (phần mở rộng embedding cache)

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

    // ── Embedding với cache ──────────────────────────────────────────────────

    /// <summary>
    /// Embed một truy vấn, kiểm tra embedding cache trước.
    /// TTL 24 giờ là phù hợp: embedding là xác định cho trọng số model cố định,
    /// nên vector cho "refund policy" không bao giờ thay đổi trừ khi
    /// embedding model được cập nhật.
    ///
    /// Yêu cầu một endpoint /ml/embed mới trên Python ML service.
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

        // Cache bất đồng bộ — không chặn đường dẫn request.
        _ = cache.SetEmbeddingAsync(embKey, result.Embedding, CancellationToken.None);

        return result.Embedding;
    }

    // ── Các method hiện có (không thay đổi) ──────────────────────────────────────────

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

## 10. Invalidation Cache — DocumentService.cs

**File**: `OpenRAG.Api/Services/DocumentService.cs` (phiên bản cập nhật đầy đủ)

Invalidation cache được gọi sau mỗi thao tác index hoặc delete thành công. Mức độ chi tiết theo collection là có chủ đích: nó bảo thủ (có thể loại bỏ các kết quả còn hợp lệ) nhưng chính xác. Invalidation chi tiết theo từng tài liệu không khả thi vì một tài liệu mới thay đổi thứ hạng của tất cả các truy vấn trên collection của nó.

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

            // Xóa cache kết quả tìm kiếm cho collection này.
            // Nội dung tài liệu mới thay đổi thứ hạng — các kết quả cũ phải bị loại bỏ.
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

            // Xóa cache kết quả tìm kiếm cho collection này.
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

        // Xóa cache kết quả tìm kiếm cho collection này.
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

## 11. Chiến Lược TTL

| Loại cache | Tiền tố key | TTL | Lý do |
|---|---|---|---|
| Kết quả tìm kiếm | `search:{collection}:{hash}` | **5 phút** | Nội dung có thể thay đổi khi index/delete; TTL ngắn giới hạn dữ liệu cũ |
| Query embedding | `emb:{hash}` | **24 giờ** | Xác định với trọng số model cố định; chỉ thay đổi khi nâng cấp model |
| Collection key index | `search-index:{collection}` | **48 giờ** | Phải tồn tại lâu hơn các kết quả tìm kiếm mà nó theo dõi |
| Collection metadata | `meta:{collection}:{hash}` | **1 phút** | Làm mới nhanh; áp dụng cho các endpoint listing/count |

TTL hoàn toàn có thể cấu hình qua `appsettings.json` (xem mục 4). Các giá trị trên là mặc định được baked vào `CacheOptions`.

**Khi nâng cấp embedding model**: khi embedding model được thay thế, tất cả các entry embedding cache 24 giờ trở nên cũ. Để giảm thiểu điều này:
1. Flush Redis cache bằng `redis-cli FLUSHDB` trong quá trình triển khai nâng cấp, hoặc
2. Thêm tên model vào embedding cache key: `emb:{modelName}:{hash}`.

Option 2 bền vững hơn. Tên model có thể được lấy từ endpoint `/ml/health` khi khởi động và lưu trữ dưới dạng giá trị cấu hình singleton.

---

## 12. Metrics và Logging

`SearchCacheService` đã phát ra các log message có cấu trúc ở cấp độ `Information` cho mỗi sự kiện HIT/MISS/SET/invalidation. Điều này đủ để giám sát phát triển và sản xuất qua một log aggregator (Seq, Datadog, v.v.).

Để có câu chuyện metrics phong phú hơn, hãy thêm các counter OpenTelemetry như bên dưới. Các slot này có thể được kết nối với một lời gọi `builder.Services.AddOpenTelemetry()` trong tương lai mà không cần thay đổi bất kỳ code nào khác.

**File**: `OpenRAG.Api/Services/Cache/CacheMetrics.cs`

```csharp
using System.Diagnostics.Metrics;

namespace OpenRAG.Api.Services.Cache;

/// <summary>
/// Đo lường hoạt động cache cho OpenTelemetry hoặc bất kỳ consumer IMeterFactory nào.
/// Đăng ký dưới dạng singleton. Metrics là no-op cho đến khi một IMeterListener được gắn vào.
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

Đăng ký trong `Program.cs`:

```csharp
builder.Services.AddSingleton<CacheMetrics>();
```

Sau đó trong `SearchCacheService`, inject `CacheMetrics` và gọi `metrics.SearchHits.Add(1)` / `metrics.SearchMisses.Add(1)` thay thế (hoặc bổ sung) các lời gọi `ILogger`. Cho đến khi một OpenTelemetry exporter được kết nối, các counter ghi vào một in-process listener với chi phí bằng không.

**Ví dụ đầu ra log** (structured JSON qua `appsettings.json` + Serilog/default provider):

```
[INF] [Cache] MISS key=search:documents:a3f9d2c1...
[INF] [Cache] SET key=search:documents:a3f9d2c1... ttl=300s collection=documents
[INF] [Cache] HIT (Redis) key=search:documents:a3f9d2c1... results=5
[INF] [Cache] Invalidated 12 Redis entries for collection=documents
```

**Tính toán hit rate** (có thể tính từ log aggregation):

```
hit_rate = SearchHits / (SearchHits + SearchMisses)
```

---

## 13. Docker Compose

Không có file `docker-compose.yml` nào trong repository. File sau đây tạo ra một stack phát triển cục bộ hoàn chỉnh bao gồm Redis service.

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

  # ── Vue frontend (chế độ dev với Vite) ────────────────────────────────────────
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

**Chạy cục bộ không có Docker**: để trống `Cache:Redis:ConnectionString` trong `appsettings.json` — hệ thống tự động fallback xuống in-process memory cache một cách graceful.

---

## 14. Đánh Đổi

### 14.1 Tính Nhất Quán vs Hiệu Năng

| Đánh đổi | Tác động | Giảm thiểu |
|---|---|---|
| Kết quả tìm kiếm cũ trong TTL 5 phút | Người dùng có thể không thấy tài liệu mới index ngay | Invalidate rõ ràng khi index/delete (đã thiết kế ở trên) |
| Memory cache L1 là per-instance | Trong triển khai nhiều instance, mỗi pod có memory cache lạnh | Redis (L2) cung cấp cache dùng chung, đã khởi động cho tất cả instance |
| Invalidation theo collection là quá rộng | Thêm một tài liệu invalidate tất cả query cho collection đó | Chấp nhận được; việc index tài liệu ít thường xuyên hơn tìm kiếm |
| Nâng cấp embedding model làm emb cache cũ | Vector sai được trả về trong 24h | Flush Redis khi deploy, hoặc thêm phiên bản model vào key |
| Các truy vấn có cardinality cao không bao giờ cache | Mỗi truy vấn độc nhất là một cache miss | Dự kiến; TTL cleanup ngăn tăng trưởng không giới hạn |

### 14.2 Định Cỡ Bộ Nhớ

Với TTL 5 phút và một `List<ChunkResult>` điển hình gồm 5 mục ở ~2 KB JSON mỗi mục (~10 KB mỗi entry):

- 1.000 truy vấn độc nhất/ngày → ~10 MB Redis memory
- 10.000 truy vấn độc nhất/ngày → ~100 MB Redis memory

Redis `maxmemory-policy allkeys-lru` được khuyến nghị để Redis tự evict các entry LRU dưới áp lực bộ nhớ mà không bị crash.

Thêm vào lệnh Redis trong `docker-compose.yml`:

```yaml
command: redis-server --save 60 1 --loglevel warning --maxmemory 256mb --maxmemory-policy allkeys-lru
```

### 14.3 In-Memory Cache vs Redis (Khi Nào Dùng Cái Nào)

| Kịch bản | Khuyến nghị |
|---|---|
| Phát triển/kiểm thử single-process | Chỉ memory cache (không cần Redis) |
| Sản xuất single-process (lưu lượng thấp) | Chỉ memory cache (vận hành đơn giản hơn) |
| Sản xuất nhiều instance (load balanced) | Redis bắt buộc để chia sẻ cache giữa các instance |
| Yêu cầu high availability | Redis Sentinel hoặc Redis Cluster |

### 14.4 Phương Án Thay Thế: Cache trong Python ML Service

ML service (`ml_service/main_ml.py`) cũng có thể cache kết quả nội bộ bằng `functools.lru_cache` hoặc Redis. Điều này sẽ trong suốt với lớp .NET. Tuy nhiên:

- Cache phía Python không hưởng lợi từ L1 memory cache trong .NET.
- Invalidation phía Python khi thay đổi tài liệu yêu cầu một lời gọi API bổ sung hoặc message queue event.
- Lớp .NET có khả năng tích hợp observability tốt hơn (structured logs, metrics).

Cache phía .NET được thiết kế ở đây là phương án ưu tiên.

### 14.5 Phương Pháp Collection Index vs Redis SCAN

`IDistributedCache` không expose các lệnh `SCAN` hoặc `KEYS`. Có hai cách tiếp cận:

| Cách tiếp cận | Ưu điểm | Nhược điểm |
|---|---|---|
| **Collection index list** (được thiết kế ở đây) | Hoạt động với bất kỳ backend `IDistributedCache` nào | Phức tạp hơn một chút; index có thể tăng trưởng không giới hạn nếu không được pruned |
| **StackExchange.Redis trực tiếp** (bypass `IDistributedCache`) | Hỗ trợ đầy đủ `SCAN + DEL` | Kết nối code với Redis; bị hỏng nếu backing store thay đổi |

Phương pháp collection index được chọn vì tính khả chuyển. Với các triển khai quy mô lớn có hàng nghìn truy vấn độc nhất mỗi collection, hãy thêm một periodic cleanup job để loại bỏ các key hết hạn khỏi index.

---

## 15. Danh Sách Kiểm Tra Triển Khai

- [ ] Thêm NuGet packages: `Microsoft.Extensions.Caching.StackExchangeRedis`, `Microsoft.Extensions.Caching.Memory`
- [ ] Tạo `OpenRAG.Api/Configuration/CacheOptions.cs`
- [ ] Tạo `OpenRAG.Api/Services/Cache/CacheKeyFactory.cs`
- [ ] Tạo `OpenRAG.Api/Services/Cache/SearchCacheService.cs`
- [ ] Tạo `OpenRAG.Api/Services/Cache/CacheMetrics.cs`
- [ ] Cập nhật `OpenRAG.Api/Program.cs` với đăng ký cache service
- [ ] Cập nhật `OpenRAG.Api/Controllers/SearchController.cs` với cache + bypass `?noCache`
- [ ] Cập nhật `OpenRAG.Api/Services/DocumentService.cs` với các lời gọi invalidation
- [ ] Cập nhật `OpenRAG.Api/Services/MlClient.cs` với embedding cache tùy chọn (yêu cầu `/ml/embed` trên ML service)
- [ ] Cập nhật `OpenRAG.Api/appsettings.json` với mục `Cache`
- [ ] Tạo `docker-compose.yml` tại repo root
- [ ] Thêm `--maxmemory` và `allkeys-lru` vào Redis trong docker-compose
- [ ] (Tùy chọn) Thêm endpoint `/ml/embed` vào `ml_service/main_ml.py`
- [ ] (Tùy chọn) Kết nối `CacheMetrics` với OpenTelemetry exporter trong `Program.cs`
