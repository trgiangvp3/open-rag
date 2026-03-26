using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Chunking;

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

// ── SPA fallback (Vue Router history mode) ────────────────────────────────
app.MapFallbackToFile("index.html");

app.Run();
