# Thiết kế API Versioning — OpenRAG

**Trạng thái**: Bản nháp
**Tác giả**: Architect Review
**Ngày**: 2026-03-26
**Dự án mục tiêu**: `OpenRAG.Api` (.NET 8, `net8.0`)

---

## Mục lục

1. [Mô tả vấn đề](#1-mô-tả-vấn-đề)
2. [Lựa chọn chiến lược versioning](#2-lựa-chọn-chiến-lược-versioning)
3. [Cài đặt NuGet — Asp.Versioning.Mvc](#3-cài-đặt-nuget--aspversioningmvc)
4. [Program.cs — Cấu hình đầy đủ](#4-programcs--cấu-hình-đầy-đủ)
5. [Chuyển đổi Controller — SearchController làm mẫu](#5-chuyển-đổi-controller--searchcontroller-làm-mẫu)
6. [Lập kế hoạch V2 — Cải tiến tìm kiếm](#6-lập-kế-hoạch-v2--cải-tiến-tìm-kiếm)
7. [Versioning phía Frontend](#7-versioning-phía-frontend)
8. [Lộ trình chuyển đổi — Không gây breaking changes](#8-lộ-trình-chuyển-đổi--không-gây-breaking-changes)
9. [Swagger / OpenAPI theo từng phiên bản](#9-swagger--openapi-theo-từng-phiên-bản)
10. [Vòng đời Deprecation](#10-vòng-đời-deprecation)
11. [Những thứ KHÔNG nên version](#11-những-thứ-không-nên-version)
12. [Lộ trình chuyển tiếp từ V1 sang V2](#12-lộ-trình-chuyển-tiếp-từ-v1-sang-v2)

---

## 1. Mô tả vấn đề

Codebase hiện tại không có lớp versioning. Cả năm controller (`SearchController`, `DocumentsController`, `ChatController`, `CollectionsController`, `HealthController`) đều expose các route trực tiếp như `/api/search` và `/api/documents`. Điều này tạo ra các ràng buộc cứng sau:

- Bất kỳ việc đổi tên field nào trong `SearchRequest` hoặc `SearchResponse` đều là breaking change ngay lập tức với mọi caller.
- Frontend (`frontend/src/api/index.ts`) bị gắn chặt với cấu trúc JSON chính xác vì `axios.create({ baseURL: '/api' })` không có bất kỳ ràng buộc version nào.
- Các tính năng tương lai — streaming kết quả tìm kiếm, batch ingestion, async job polling — không thể được thêm vào một cách sạch sẽ mà không hoặc (a) làm ô nhiễm các endpoint hiện có với các tham số tùy chọn, hoặc (b) thay đổi hành vi một cách lặng lẽ.
- Không có cơ chế để cảnh báo client rằng một hành vi sẽ bị loại bỏ.

---

## 2. Lựa chọn chiến lược versioning

### So sánh các phương án

| Phương án | URL Path `/api/v1/search` | Header `API-Version: 1` | Query Param `?api-version=1` |
|---|---|---|---|
| Tính rõ ràng | Tường minh — thấy được trong browser, logs, proxies | Ẩn — cần kiểm tra headers | Thấy được trong URL, nhưng khó đọc |
| Khả năng cache | Tốt — CDN/proxy có thể cache theo đường dẫn | Kém — phải dùng Vary trên header | Chấp nhận được, nhưng query strings vô hiệu hóa nhiều lớp cache |
| Độ thuần REST | Có tranh luận — một số cho rằng version phá vỡ resource identity | Gần nhất với lý thuyết REST | Phổ biến trong các API Azure/Microsoft |
| Dễ triển khai | Đơn giản — chỉ thay đổi route template | Yêu cầu middleware đọc header | Thêm filter đơn giản |
| Ràng buộc Frontend | Tường minh — caller phải biết version trong URL | Caller phải đặt header trong mỗi request | Caller phải thêm param vào mỗi request |
| Công cụ Swagger | Tốt nhất — tự nhiên tạo docs riêng theo prefix | Cần cấu hình thêm | Hoạt động nhưng làm URL trong docs lộn xộn |
| Phù hợp cho công cụ nội bộ | Có — đơn giản cho các thành viên trong nhóm | Có | Có |
| Phù hợp cho API công khai | Có | Có — URL sạch hơn | Ít được ưa thích hơn |

### Khuyến nghị: URL Path versioning (`/api/v1/...`)

**Lý do cụ thể cho OpenRAG:**

1. **Bối cảnh công cụ nội bộ.** OpenRAG hiện là một nền tảng RAG nội bộ với một frontend Vue được tích hợp sẵn và một Python ML service là những caller duy nhất của public API. URL-path versioning ít gây tải nhận thức nhất cho nhóm nhỏ và bất kỳ ai đọc logs hoặc curl endpoint thủ công.

2. **Cầu nối migration không tốn công.** Các route không có version hiện tại (`/api/search`, `/api/documents`, v.v.) có thể được giữ nguyên như alias của v1 bằng cách map với `MapToApiVersion("1.0")`. Điều này có nghĩa là thêm versioning không cần bất kỳ thay đổi frontend nào vào ngày đầu tiên.

3. **Tách biệt Swagger tự nhiên.** Swashbuckle tự động tạo các trang Swagger UI riêng biệt theo route prefix khi sử dụng URL-path versioning, không cần tùy chỉnh filter phức tạp.

4. **Rõ ràng cho Frontend.** Khi V2 ra mắt với streaming search, frontend gọi tường minh `/api/v2/search` cho đường dẫn mới và `/api/v1/search` cho đường dẫn ổn định. Sự phân biệt này rõ ràng trong code review và network traces.

5. **Thân thiện với Proxy và Load Balancer.** Nếu OpenRAG có thêm lớp nginx hoặc Traefik, các quy tắc định tuyến dựa trên `/api/v1/` hoặc `/api/v2/` là các path matcher đơn giản không cần kiểm tra header.

**Pattern đã chọn:** `https://host/api/v{major}/resource`

Minor version (1.0 so với 1.1) được theo dõi nội bộ cho các thay đổi bổ sung không gây breaking. Chỉ các thay đổi major version mới xuất hiện trong URL prefix.

---

## 3. Cài đặt NuGet — Asp.Versioning.Mvc

### Các package cần thêm

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

`Asp.Versioning.Mvc` là phiên bản kế tiếp của `Microsoft.AspNetCore.Mvc.Versioning` (hiện đã được lưu trữ). Nó được duy trì bởi nhóm .NET và hỗ trợ .NET 8 một cách tự nhiên. `Asp.Versioning.Mvc.ApiExplorer` cung cấp `IApiVersionDescriptionProvider` — cần thiết để tạo tài liệu Swagger theo từng phiên bản.

**Không sử dụng** `Microsoft.AspNetCore.Mvc.Versioning` đã được lưu trữ — nó không có bản phát hành nào kể từ năm 2022 và có các vấn đề tương thích chưa được giải quyết với .NET 8.

---

## 4. Program.cs — Cấu hình đầy đủ

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

// ── LLM client (tùy chọn) ─────────────────────────────────────────────────
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

// ── Các service ứng dụng ──────────────────────────────────────────────────
builder.Services.AddSingleton<MarkdownChunker>();
builder.Services.AddScoped<DocumentService>();
builder.Services.AddScoped<CollectionService>();
builder.Services.AddScoped<ChatService>();

// ── SignalR ───────────────────────────────────────────────────────────────
builder.Services.AddSignalR();

// ── API Versioning ────────────────────────────────────────────────────────
builder.Services.AddApiVersioning(options =>
{
    // Các client không gửi chỉ báo version nào được giả định là nhắm vào v1.0.
    // Điều này giữ cho tất cả các /api/search, /api/documents, v.v. hiện có hoạt động
    // mà không cần thay đổi frontend.
    options.DefaultApiVersion = new ApiVersion(1, 0);
    options.AssumeDefaultVersionWhenUnspecified = true;

    // Bao gồm các header api-supported-versions và api-deprecated-versions
    // trong mọi response để client có thể tự khám phá bức tranh version.
    options.ReportApiVersions = true;

    // Chiến lược chính: URL path segment (/api/v1/, /api/v2/)
    // Chiến lược phụ: header (API-Version: 1.0) — hữu ích cho các công cụ
    // không thể sửa đổi URL path.
    options.ApiVersionReader = ApiVersionReader.Combine(
        new UrlSegmentApiVersionReader(),
        new HeaderApiVersionReader("API-Version")
    );
})
.AddMvc()                   // tích hợp với ASP.NET Core MVC controller discovery
.AddApiExplorer(options =>
{
    // Định dạng chuỗi version thành "v1", "v2" trong dropdown Swagger.
    options.GroupNameFormat = "'v'VVV";

    // Thay thế tham số route {version} trong các template [Route]
    // để các controller không cần hard-code nó.
    options.SubstituteApiVersionInUrl = true;
});

// ── Controllers + CORS ───────────────────────────────────────────────────
builder.Services.AddControllers();
builder.Services.AddCors(o => o.AddDefaultPolicy(p =>
    p.WithOrigins("http://localhost:5173", "http://localhost:8000")
     .AllowAnyMethod()
     .AllowAnyHeader()
     .AllowCredentials()));

// ── Swagger / OpenAPI (tài liệu theo từng phiên bản) ─────────────────────────────
builder.Services.AddTransient<IConfigureOptions<SwaggerGenOptions>, ConfigureSwaggerOptions>();
builder.Services.AddSwaggerGen(options =>
{
    // Bao gồm các XML doc comments nếu bạn thêm <GenerateDocumentationFile>true</GenerateDocumentationFile>
    // vào .csproj.  An toàn để để lại ngay cả trước khi file tồn tại.
    var xmlFile = $"{System.Reflection.Assembly.GetExecutingAssembly().GetName().Name}.xml";
    var xmlPath = Path.Combine(AppContext.BaseDirectory, xmlFile);
    if (File.Exists(xmlPath))
        options.IncludeXmlComments(xmlPath);
});

var app = builder.Build();

// ── Migrate DB khi khởi động ─────────────────────────────────────────────
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

    // Hiển thị một Swagger UI endpoint cho mỗi phiên bản API được phát hiện.
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

### Lớp helper ConfigureSwaggerOptions

Đặt lớp này trong `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Infrastructure/ConfigureSwaggerOptions.cs`:

```csharp
using Asp.Versioning.ApiExplorer;
using Microsoft.Extensions.Options;
using Microsoft.OpenApi.Models;
using Swashbuckle.AspNetCore.SwaggerGen;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Tự động tạo một tài liệu Swagger cho mỗi phiên bản API được phát hiện.
/// Được thực thi khi khởi động sau khi tất cả các phiên bản API đã được đăng ký.
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
                "\n\n**Phiên bản API này đã bị deprecated.** " +
                "Nó sẽ bị xóa 6 tháng sau ngày deprecation. " +
                "Vui lòng chuyển sang phiên bản mới nhất.";
        }

        return info;
    }
}
```

---

## 5. Chuyển đổi Controller — SearchController làm mẫu

Phần này trình bày việc chuyển đổi hoàn chỉnh `SearchController`. Áp dụng cùng pattern cho tất cả các controller khác.

### Các quyết định chính trong quá trình chuyển đổi này

- **Legacy route** (`/api/search`) được giữ nguyên qua một thuộc tính `[Route]` thứ hai để frontend Vue không bị hỏng.
- **Versioned route** (`/api/v1/search`) là đường dẫn chính thức từ nay về sau.
- Cả hai route đều map đến cùng một class controller và cùng một action method — không trùng lặp code.
- Thuộc tính `[MapToApiVersion]` gắn một action cụ thể vào một version cụ thể khi controller hỗ trợ nhiều version.

### SearchController đã cập nhật (V1)

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Controllers/V1/SearchController.cs`

```csharp
using Asp.Versioning;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers.V1;

/// <summary>
/// Endpoint tìm kiếm tài liệu — V1.
/// Hỗ trợ truy xuất semantic và hybrid với tùy chọn tạo câu trả lời bằng LLM.
/// </summary>
[ApiController]
[ApiVersion("1.0")]
// Route có version chính thức — các client mới nên dùng route này.
[Route("api/v{version:apiVersion}/search")]
// Route không có version (legacy) — giữ lại để tương thích ngược.
// Các request đến /api/search được coi là v1.0 (AssumeDefaultVersionWhenUnspecified = true).
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
{
    /// <summary>
    /// Tìm kiếm tài liệu bằng chiến lược truy xuất đã được cấu hình.
    /// </summary>
    /// <param name="req">Các tham số tìm kiếm.</param>
    /// <param name="ct">Cancellation token.</param>
    /// <returns>Danh sách các document chunk được xếp hạng, tùy chọn có câu trả lời do LLM tạo ra.</returns>
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

### Ghi chú về pattern dual-route

Khi `Asp.Versioning` nhận được request đến `/api/search` (không có segment version), nó áp dụng version mặc định (1.0) vì `AssumeDefaultVersionWhenUnspecified = true`. `UrlSegmentApiVersionReader` đọc ràng buộc `{version:apiVersion}` từ route template, nhưng vì route không có version không có segment như vậy, giá trị mặc định sẽ được áp dụng. Cả hai route đều resolve đến cùng một action. Response headers sẽ bao gồm:

```
api-supported-versions: 1.0
```

Điều này cho frontend (hoặc bất kỳ client nào khác) biết rằng v1.0 tồn tại và legacy path thực ra là v1.0 bên dưới.

### Áp dụng cùng pattern cho tất cả các controller khác

| File gốc | Vị trí mới | Ghi chú |
|---|---|---|
| `Controllers/SearchController.cs` | `Controllers/V1/SearchController.cs` | Mẫu ở trên |
| `Controllers/DocumentsController.cs` | `Controllers/V1/DocumentsController.cs` | Cùng pattern dual-route |
| `Controllers/ChatController.cs` | `Controllers/V1/ChatController.cs` | Cùng pattern dual-route |
| `Controllers/CollectionsController.cs` | `Controllers/V1/CollectionsController.cs` | Cùng pattern dual-route |
| `Controllers/HealthController.cs` | `Controllers/HealthController.cs` | Health không phụ thuộc version — xem mục 11 |

Việc chuyển vào thư mục con `V1/` chỉ mang tính tổ chức. C# namespaces (`namespace OpenRAG.Api.Controllers.V1`) được cập nhật nhưng ASP.NET Core controller discovery không phụ thuộc vào namespace.

---

## 6. Lập kế hoạch V2 — Cải tiến tìm kiếm

V2 giới thiệu ba khả năng mới không thể được thêm vào các endpoint V1 một cách gọn gàng mà không thay đổi contract của chúng:

### 6.1 Tóm tắt tính năng

| Tính năng | Lý do V1 không thể tích hợp | Cách tiếp cận V2 |
|---|---|---|
| **Streaming kết quả tìm kiếm qua SSE** | V1 trả về một JSON blob `SearchResponse` hoàn chỉnh. Streaming yêu cầu `Content-Type: text/event-stream` và response body theo dạng chunked — không tương thích với response shape hiện tại. | `POST /api/v2/search/stream` |
| **Batch queries** | Chấp nhận `queries: string[]` thay vì `query: string` là một breaking schema change. | `POST /api/v2/search/batch` |
| **Async job pattern** | Đối với các thao tác chậm (reranking + generation trên các collection lớn), trả về `jobId` ngay lập tức và expose endpoint để poll. Caller V1 mong đợi response đồng bộ. | `POST /api/v2/search/jobs` + `GET /api/v2/search/jobs/{jobId}` |

### 6.2 Model Request/Response V2

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
    /// Khi true, endpoint stream SSE events thay vì trả về
    /// một JSON body duy nhất. Yêu cầu Accept: text/event-stream.
    /// </summary>
    bool Stream = false
);

/// <summary>V2 batch search request — nhiều query trong một lần round-trip.</summary>
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
/// Kết quả tìm kiếm V2 — thêm metadata tài liệu nguồn ở cấp độ trên
/// và tách answer thành một object có cấu trúc.
/// </summary>
public record ChunkResultV2(
    string Text,
    double Score,
    Dictionary<string, object> Metadata,
    double? RerankScore = null,
    /// <summary>Nhãn nguồn dễ đọc được tạo từ metadata.</summary>
    string? SourceLabel = null
);

public record GeneratedAnswer(string Text, List<int> Citations, double ConfidenceScore);

public record SearchV2Response(
    string Query,
    List<ChunkResultV2> Results,
    int Total,
    /// <summary>Null khi Generate = false hoặc LLM chưa được cấu hình.</summary>
    GeneratedAnswer? Answer = null
);

/// <summary>Batch response — một entry SearchV2Response cho mỗi query.</summary>
public record BatchSearchResponse(List<BatchSearchItem> Items, int TotalQueries);

public record BatchSearchItem(string Query, List<ChunkResultV2> Results, int Total);

/// <summary>Được trả về ngay lập tức bởi endpoint async job.</summary>
public record SearchJobAccepted(
    Guid JobId,
    string StatusUrl,
    string Status = "queued"
);

/// <summary>Được poll qua GET /api/v2/search/jobs/{jobId}.</summary>
public record SearchJobResult(
    Guid JobId,
    string Status,           // queued | running | completed | failed
    SearchV2Response? Result = null,
    string? Error = null,
    DateTimeOffset? CompletedAt = null
);
```

### 6.3 Skeleton V2 SearchController

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
/// Endpoint tìm kiếm tài liệu — V2.
/// Thêm streaming (SSE), batch queries và async job pattern.
/// </summary>
[ApiController]
[ApiVersion("2.0")]
[Route("api/v{version:apiVersion}/search")]
public class SearchController(
    MlClient ml,
    LlmClient llm,
    SearchJobService jobService    // service mới chỉ dành cho V2
) : ControllerBase
{
    // ── Tìm kiếm đồng bộ (synchronous) — cùng ngữ nghĩa với V1 ────────────────
    // Caller không dùng streaming sẽ dùng endpoint này. Response shape đã thay đổi:
    // ChunkResultV2 thêm SourceLabel; Answer là một object có cấu trúc.

    /// <summary>Tìm kiếm đồng bộ với một query duy nhất.</summary>
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
    // Client gửi Accept: text/event-stream. Server stream
    // từng kết quả chunk dưới dạng SSE events khi chúng được truy xuất,
    // theo sau là một event "done" cuối cùng với câu trả lời được tạo ra (nếu có).
    //
    // Định dạng SSE event cho mỗi chunk:
    //   event: chunk
    //   data: { "text": "...", "score": 0.92, "metadata": {...} }
    //
    // Event cuối cùng:
    //   event: done
    //   data: { "answer": "...", "citations": [1, 2] }

    /// <summary>
    /// Tìm kiếm streaming qua Server-Sent Events.
    /// Đặt header Accept: text/event-stream. Kết quả được đẩy khi chúng đến.
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
    // Chấp nhận tối đa 20 query và trả về kết quả cho tất cả chúng.
    // Mỗi query chạy độc lập đối với ML service.
    // Xem xét thêm giới hạn concurrency (SemaphoreSlim) cho môi trường production.

    /// <summary>
    /// Thực thi nhiều search query trong một request duy nhất.
    /// Tối đa 20 query mỗi batch.
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
    // Dành cho các tìm kiếm chạy lâu (collection lớn, reranking + generation chậm).
    // Client nhận jobId ngay lập tức và poll để chờ hoàn thành.
    // SearchJobService phải được triển khai như một hàng đợi nền (ví dụ: Channel<T>
    // được hỗ trợ bởi hosted service hoặc Hangfire để lưu trữ bền vững khi restart).

    /// <summary>
    /// Gửi một tìm kiếm dưới dạng async job. Trả về ngay lập tức với một jobId.
    /// Poll GET /api/v2/search/jobs/{jobId} để lấy kết quả.
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

    /// <summary>Poll để lấy kết quả của một async search job.</summary>
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

### 6.4 Stub SearchJobService

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Services/V2/SearchJobService.cs`

```csharp
using OpenRAG.Api.Models.Dto.Requests.V2;
using OpenRAG.Api.Models.Dto.Responses.V2;

namespace OpenRAG.Api.Services.V2;

/// <summary>
/// Quản lý các async search job. Thay thế dictionary in-memory bằng
/// một store bền vững (ví dụ: SQLite table hoặc Redis) trước khi đưa vào production.
/// </summary>
public sealed class SearchJobService(MlClient ml, LlmClient llm, ILogger<SearchJobService> logger)
{
    // Được key bởi jobId. Trong production, lưu trữ trong SQLite hoặc hàng đợi bên ngoài.
    private readonly Dictionary<Guid, SearchJobResult> _jobs = new();
    private readonly SemaphoreSlim _lock = new(1, 1);

    public async Task<Guid> EnqueueAsync(SearchV2Request req, CancellationToken ct = default)
    {
        var jobId = Guid.NewGuid();

        await _lock.WaitAsync(ct);
        _jobs[jobId] = new SearchJobResult(jobId, "queued");
        _lock.Release();

        // Fire-and-forget trên thread pool.
        // Trong production: dùng IHostedService + Channel<T>, hoặc Hangfire.
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

Đăng ký trong `Program.cs`:

```csharp
builder.Services.AddSingleton<SearchJobService>();
```

---

## 7. Versioning phía Frontend

File: `d:/Works/trgiangvp3/open-rag/frontend/src/api/index.ts`

Chiến lược là:

1. Giữ nguyên instance `api` hiện tại (baseURL `/api`). Tất cả các lời gọi hàm hiện tại tiếp tục hoạt động mà không cần sửa đổi. Đây là **lớp tương thích V1**.
2. Thêm alias `apiV1` (tường minh) và instance `apiV2` trỏ đến `/api/v2`.
3. Export các nhóm hàm theo version. Nhóm V1 là các hàm hiện có, không thay đổi. Nhóm V2 giới thiệu các signature mới.
4. Factory `versionedApi` cho phép gọi một version tùy ý nếu cần.

```typescript
import axios, { type AxiosInstance } from 'axios'

// ── Axios instances ────────────────────────────────────────────────────────

/**
 * Legacy client không có version — map đến /api (được server coi là v1).
 * Tiếp tục dùng client này cho tất cả code hiện có.  KHÔNG xóa.
 */
export const api = axios.create({ baseURL: '/api' })

/**
 * Client V1 tường minh — tương đương với `api` nhưng dùng path có version chính thức.
 * Ưu tiên dùng trong code V1 mới để mục đích rõ ràng hơn.
 */
export const apiV1 = axios.create({ baseURL: '/api/v1' })

/**
 * Client V2 — trỏ đến các endpoint V2 (streaming, batch, async jobs).
 * Chỉ dùng các hàm được export từ namespace `v2`.
 */
export const apiV2 = axios.create({ baseURL: '/api/v2' })

/**
 * Factory để tạo một API client có version theo yêu cầu.
 * Hữu ích cho các component cần chuyển đổi version lúc runtime.
 */
export function versionedApi(version: 1 | 2): AxiosInstance {
  return version === 2 ? apiV2 : apiV1
}

// ── Các type dùng chung ───────────────────────────────────────────────────

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

// ── Các type chỉ dành cho V2 ──────────────────────────────────────────────

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

// ── Các hàm API V1 (không thay đổi so với triển khai hiện tại) ───────────────
// Tất cả các lời gọi đều đi qua client `api` legacy nên các component hiện có
// không cần sửa đổi gì.

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

// ── Các hàm API V2 ───────────────────────────────────────────────────────

export const v2 = {
  /** Tìm kiếm V2 đồng bộ (synchronous) với response shape phong phú hơn. */
  search: (
    query: string,
    collection: string,
    topK: number,
    opts: SearchOptions = {}
  ) => apiV2.post<SearchV2Response>('/search', { query, collection, topK, ...opts }),

  /**
   * Tìm kiếm SSE streaming.
   * Trả về một native EventSource — caller gắn các event listener.
   * Lưu ý: EventSource chỉ hỗ trợ GET; dùng fetchEventSource từ
   * @microsoft/fetch-event-source cho POST-based SSE.
   *
   * Cách dùng:
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

    // Dùng fetch trực tiếp cho POST + SSE.
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
        // Raw SSE text — phân tích và dispatch custom events trên window
        const text = decoder.decode(value)
        // (logic phân tích được ủy quyền cho component tiêu thụ)
        window.dispatchEvent(new CustomEvent('rag:sse', { detail: text }))
      }
    })

    return controller
  },

  /** Batch search — tối đa 20 query trong một request. */
  batchSearch: (req: BatchSearchRequest) =>
    apiV2.post<BatchSearchResponse>('/search/batch', req),

  /** Gửi một async search job. Poll getJob() để lấy kết quả. */
  submitJob: (
    query: string,
    collection: string,
    topK: number,
    opts: SearchOptions = {}
  ) => apiV2.post<SearchJobAccepted>('/search/jobs', { query, collection, topK, ...opts }),

  /** Poll để lấy kết quả của một async search job. */
  getJob: (jobId: string) =>
    apiV2.get<SearchJobResult>(`/search/jobs/${jobId}`),
}
```

---

## 8. Lộ trình chuyển đổi — Không gây breaking changes

Phần này cho thấy chính xác cách versioning được thêm vào mà không cần chạm vào bất kỳ route nào mà frontend Vue hiện tại đang sử dụng.

### Giai đoạn 1 — Thêm hạ tầng versioning (không thay đổi gì với client)

1. Thêm các NuGet package vào `OpenRAG.Api.csproj`.
2. Cập nhật `Program.cs` với các lời gọi `AddApiVersioning` / `AddMvc` / `AddApiExplorer` được chỉ ra trong mục 4.
3. Di chuyển mỗi controller vào thư mục con `V1/` và cập nhật namespace của nó.
4. Thêm cả hai thuộc tính `[Route("api/v{version:apiVersion}/resource")]` và `[Route("api/resource")]` vào mỗi controller.
5. Thêm `[ApiVersion("1.0")]` vào mỗi controller.
6. Deploy. Kiểm tra:
   - `POST /api/search` — vẫn hoạt động, trả về HTTP 200.
   - Response headers bây giờ bao gồm `api-supported-versions: 1.0`.
   - `POST /api/v1/search` — cũng hoạt động (đường dẫn chính thức mới).
   - Swagger UI tại `/swagger` hiển thị một tài liệu: "OpenRAG API v1".

### Giai đoạn 2 — Giới thiệu V2 controllers (chỉ bổ sung thêm)

1. Thêm V2 controllers vào `Controllers/V2/`.
2. Thêm V2 service (`SearchJobService`) vào `Program.cs`.
3. Deploy. Kiểm tra:
   - Tất cả V1 routes vẫn trả về HTTP 200 — không có regression.
   - `POST /api/v2/search` hoạt động.
   - `POST /api/v2/search/stream` hoạt động (SSE).
   - Swagger UI hiển thị hai tài liệu: "OpenRAG API v1" và "OpenRAG API v2".
   - Response headers bao gồm `api-supported-versions: 1.0, 2.0`.

### Giai đoạn 3 — Migration frontend lên V2 (tùy chọn, tăng dần)

Cập nhật `frontend/src/api/index.ts` như được chỉ ra trong mục 7. Vì các hàm `search()`, `uploadFile()`, v.v. hiện có không thay đổi và vẫn gọi `api` (trỏ đến `/api`), không component Vue nào cần sửa đổi ở thời điểm này. Các hàm V2 là các export mới, bổ sung thêm.

Chuyển đổi từng component Vue sang dùng `v2.search()` hoặc `v2.searchStream()` khi tính năng sẵn sàng cho bản build dành cho người dùng.

### Tham chiếu khai báo dual-route

Mỗi controller tuân theo đúng pattern này. Route không có version là alias tương thích ngược; route có version là đường dẫn chính thức từ nay về sau.

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

## 9. Swagger / OpenAPI theo từng phiên bản

Lớp `ConfigureSwaggerOptions` được chỉ ra trong mục 4 xử lý việc tạo tài liệu theo từng phiên bản. Dưới đây là toàn bộ Swagger pipeline để rõ ràng hơn.

### Những gì được tạo ra

Sau khi thêm cả V1 và V2 controllers:

| URL | Nội dung |
|---|---|
| `/swagger/v1/swagger.json` | Tài liệu OpenAPI cho tất cả controller có `[ApiVersion("1.0")]` |
| `/swagger/v2/swagger.json` | Tài liệu OpenAPI cho tất cả controller có `[ApiVersion("2.0")]` |
| `/swagger` | Swagger UI với dropdown để chuyển đổi giữa v1 và v2 |

### Document filter để ẩn các operation deprecated (tùy chọn)

File: `d:/Works/trgiangvp3/open-rag/OpenRAG.Api/Infrastructure/DeprecatedOperationFilter.cs`

```csharp
using Microsoft.OpenApi.Models;
using Swashbuckle.AspNetCore.SwaggerGen;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Đánh dấu các operation là deprecated trong tài liệu OpenAPI khi
/// controller method được trang trí với [Obsolete].
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

Đăng ký trong `Program.cs` bên trong `AddSwaggerGen`:

```csharp
builder.Services.AddSwaggerGen(options =>
{
    options.OperationFilter<DeprecatedOperationFilter>();
    // ... các tùy chọn hiện có
});
```

### Mẫu Swagger JSON của V1 (rút gọn)

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

## 10. Vòng đời Deprecation

### Định nghĩa chính sách

| Giai đoạn | Thời gian | Hành động |
|---|---|---|
| **Active** | Không xác định | Phiên bản đang hiện hành. Không có cảnh báo. |
| **Deprecated** | Tối thiểu 6 tháng | `Deprecated = true` trên `[ApiVersion]`. Response header `api-deprecated-versions` xuất hiện. Tài liệu Swagger chú thích phiên bản. `[Obsolete]` trên các controller action bị ảnh hưởng. |
| **Sunset** | Sau cửa sổ 6 tháng | HTTP `410 Gone` hoặc redirect đến phiên bản mới. Tùy chọn thêm response header `Sunset` theo RFC 8594. |
| **Removed** | Sau sunset | Controller và DTOs bị xóa khỏi codebase. |

### Cách đánh dấu V1 là deprecated

Khi V2 ổn định và V1 bước vào cửa sổ deprecation 6 tháng, hãy cập nhật các V1 controller:

```csharp
// Controllers/V1/SearchController.cs

[ApiController]
[ApiVersion("1.0", Deprecated = true)]   // <-- đặt Deprecated = true
[Route("api/v{version:apiVersion}/search")]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
{
    /// <summary>
    /// Tìm kiếm tài liệu bằng chiến lược truy xuất đã được cấu hình.
    /// DEPRECATED: Hãy chuyển sang dùng POST /api/v2/search.
    /// Endpoint này sẽ bị xóa vào ngày 2027-01-01.
    /// </summary>
    [HttpPost]
    [MapToApiVersion("1.0")]
    [Obsolete("Use POST /api/v2/search. V1 will be removed on 2027-01-01.")]
    public async Task<IActionResult> Search(
        [FromBody] SearchRequest req,
        CancellationToken ct = default)
    {
        // triển khai hiện có không thay đổi
    }
}
```

Hiệu ứng trên responses sau khi đặt `Deprecated = true`:

```
api-supported-versions: 1.0, 2.0
api-deprecated-versions: 1.0
```

Client nhận được tín hiệu có thể đọc được bằng máy rằng V1 sắp bị loại bỏ. Không cần thực hiện hành động nào trong logic ứng dụng — `Asp.Versioning` tự động phát ra header.

### Thêm header Sunset (RFC 8594)

Để rõ ràng hơn, hãy thêm một response filter để gắn header `Sunset` vào tất cả V1 responses trong suốt giai đoạn deprecation:

```csharp
// Infrastructure/SunsetHeaderFilter.cs
using Asp.Versioning;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace OpenRAG.Api.Infrastructure;

/// <summary>
/// Gắn header Sunset (RFC 8594) vào responses từ các phiên bản API đã deprecated,
/// thông báo cho client ngày chính xác phiên bản sẽ bị xóa.
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

Áp dụng cho V1 controller đã deprecated:

```csharp
[ApiVersion("1.0", Deprecated = true)]
[Sunset("2027-01-01")]   // client thấy: Sunset: 2027-01-01
public class SearchController(...) { ... }
```

### Checklist giao tiếp khi deprecated một phiên bản

- [ ] Đặt `Deprecated = true` trên tất cả các thuộc tính `[ApiVersion("1.0")]`.
- [ ] Thêm `[Obsolete]` vào tất cả V1 action methods với hướng dẫn migration.
- [ ] Áp dụng `[Sunset("YYYY-MM-DD")]` cho tất cả V1 controller.
- [ ] Cập nhật mô tả Swagger trong `ConfigureSwaggerOptions` (đã được xử lý — nó thêm thông báo deprecation khi `description.IsDeprecated` là true).
- [ ] Thông báo ngày sunset trong release notes / changelog.
- [ ] Sau ngày sunset: trả về `410 Gone` từ V1 routes (thay thế thân action bằng `return StatusCode(410, new { detail = "This API version has been removed. Use /api/v2/..." })`).

---

## 11. Những thứ KHÔNG nên version

### ML service nội bộ (`http://localhost:8001`)

Python FastAPI ML service (`ml_service/`) là một chi tiết triển khai nội bộ — một backend riêng tư mà chỉ `MlClient` trong lớp .NET gọi. Nó không phải là public API. Nó không nên được version với `Asp.Versioning` hay bất kỳ contract version công khai nào.

Lý do:
- Giao diện ML service thay đổi cùng với `MlClient` trong một đơn vị deployment duy nhất. Không có consumer độc lập nào cần được bảo vệ khỏi breaking changes.
- Thêm version vào `/ml/search` v.v. tạo ra chi phí vận hành mà không có lợi ích.
- Nếu ML service cần hỗ trợ nhiều phiên bản thuật toán đồng thời (ví dụ: bge-m3 so với một mô hình tương lai), hãy dùng feature flags hoặc tham số cấu hình bên trong các endpoint hiện có, không dùng URL versioning.

### Sự kiện SignalR / WebSocket (`/ws/progress`)

`ProgressHub` gửi các sự kiện `progress` qua SignalR trong quá trình lập chỉ mục tài liệu. Các sự kiện này:
- Tồn tại trong thời gian ngắn (chỉ trong một thao tác ingest duy nhất).
- Chỉ được tiêu thụ bởi frontend Vue đi kèm.
- Không phải là một phần của bất kỳ contract tích hợp bên ngoài nào.

Các schema sự kiện WebSocket/SignalR nên phát triển mà không có số version. Các breaking changes đối với event payload được phối hợp bằng cách deploy cả frontend lẫn backend cùng nhau (chúng được phục vụ từ cùng một .NET host).

Nếu một tình huống tương lai yêu cầu contract WebSocket lâu dài với các client bên ngoài, hãy giới thiệu tham số query `hub-version` ở cấp độ URL kết nối — không thông qua `Asp.Versioning`.

### Health endpoint (`/api/health`)

Health check (`HealthController`) được tiêu thụ bởi các công cụ hạ tầng (load balancer, Docker health check, uptime monitor). Chúng phải ổn định và không phụ thuộc version. Endpoint này ở lại `/api/health` mà không có version prefix. Áp dụng `[ApiVersionNeutral]` để opt nó ra khỏi version routing:

```csharp
// Controllers/HealthController.cs
using Asp.Versioning;

[ApiController]
[ApiVersionNeutral]           // phản hồi tất cả các version và các request không có version
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

## 12. Lộ trình chuyển tiếp từ V1 sang V2

### Dòng thời gian (giả sử công việc V2 bắt đầu Q2 2026)

```
Q2 2026  ─── Giai đoạn 1: Hạ tầng versioning (2 tuần)
              Thêm Asp.Versioning packages.
              Chuyển controllers vào thư mục V1/, dual-route.
              Cập nhật Program.cs.
              Thêm tài liệu Swagger theo từng phiên bản.
              KHÔNG cần thay đổi frontend.

Q3 2026  ─── Giai đoạn 2: Triển khai tính năng V2 (6–8 tuần)
              Triển khai V2 SearchController (stream, batch, async job).
              Triển khai V2 SearchJobService.
              Cập nhật frontend api/index.ts với namespace v2.
              Migrate tab Search trong Vue sang dùng v2.searchStream() khi
              người dùng bật toggle "Stream results" (feature flag).

Q4 2026  ─── Giai đoạn 3: Thông báo deprecation V1
              Đặt Deprecated = true trên tất cả V1 controllers.
              Thêm header [Sunset("2027-07-01")].
              Thông báo cửa sổ deprecation 6 tháng trong changelog.
              Migrate các Vue component còn lại sang các hàm V2.
              Header api-deprecated-versions: 1.0 xuất hiện trong tất cả responses.

Q1–Q2 2027 ─ Giai đoạn 4: Sunset V1
              Sau 2027-07-01: thay thế thân V1 action bằng 410 Gone.
              Giữ alias [Route("api/search")] trả về 410 Gone cho
              bất kỳ caller V1 nào chưa được phát hiện (không xóa route cho đến khi
              access logs xác nhận không có lượt truy cập nào).

Q3 2027  ─── Giai đoạn 5: Xóa V1
              Xóa V1 controllers, V1 DTOs.
              Xóa các thuộc tính [Route] không có version (legacy).
              Xóa logic header api-deprecated-versions.
              Tag git commit: api/v1-removed.
```

### Giảm thiểu rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Các client chưa được phát hiện đang gọi `/api/search` | Legacy routes trả về HTTP 200 (hành vi V1) vô thời hạn cho đến Giai đoạn 4. Response header `api-deprecated-versions: 1.0` cảnh báo sớm cho các client. Theo dõi access logs cho V1 route hits trước ngày sunset. |
| SSE không được hỗ trợ bởi một số client | V2 streaming là bổ sung thêm. `POST /api/v2/search` đồng bộ cung cấp cùng kết quả mà không cần streaming. Các client opt in vào streaming bằng cách gọi `POST /api/v2/search/stream`. |
| `SearchJobService` in-memory mất jobs khi restart | Chấp nhận được ở Giai đoạn 2 (phát triển). Trước Giai đoạn 3 (production), hãy migrate dictionary `_jobs` sang SQLite table được hỗ trợ bởi EF Core `DbContext`. |
| Response shape V2 không tương thích với TypeScript types hiện có | Các type V2 (`SearchV2Response`, `ChunkResultV2`, `GeneratedAnswer`) tách biệt với các type V1 trong `api/index.ts`. Không có V1 interface nào bị sửa đổi. |

---

## Phụ lục — Cấu trúc file sau khi triển khai đầy đủ

```
OpenRAG.Api/
├── Controllers/
│   ├── HealthController.cs          # [ApiVersionNeutral] — vị trí không thay đổi
│   ├── V1/
│   │   ├── SearchController.cs      # [ApiVersion("1.0")] dual-route
│   │   ├── DocumentsController.cs   # [ApiVersion("1.0")] dual-route
│   │   ├── ChatController.cs        # [ApiVersion("1.0")] dual-route
│   │   └── CollectionsController.cs # [ApiVersion("1.0")] dual-route
│   └── V2/
│       └── SearchController.cs      # [ApiVersion("2.0")] /api/v2/search
├── Infrastructure/
│   ├── ConfigureSwaggerOptions.cs   # bộ tạo tài liệu Swagger theo từng phiên bản
│   ├── DeprecatedOperationFilter.cs # đánh dấu op [Obsolete] là deprecated trong OAS
│   └── SunsetAttribute.cs          # thêm response header Sunset + Deprecation
├── Models/
│   └── Dto/
│       ├── Requests/
│       │   ├── SearchRequest.cs     # V1 — không thay đổi
│       │   ├── ChatRequest.cs       # V1 — không thay đổi
│       │   └── V2/
│       │       └── SearchV2Request.cs
│       └── Responses/
│           ├── SearchResponse.cs    # V1 — không thay đổi
│           └── V2/
│               └── SearchV2Response.cs
└── Services/
    └── V2/
        └── SearchJobService.cs

frontend/src/api/
└── index.ts                         # đã cập nhật: namespace v2, instances apiV1/apiV2
```
