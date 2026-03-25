using System.Net.Http.Json;
using System.Text.Json;
using OpenRAG.Api.Models.Dto.Responses;

namespace OpenRAG.Api.Services;

public record MlChunkInput(string Text, Dictionary<string, string> Metadata);
public record MlIndexRequest(string DocumentId, string Collection, List<MlChunkInput> Chunks);
public record MlIndexResponse(string DocumentId, int ChunkCount, bool Ok);
public record MlSearchRequest(string Query, string Collection, int TopK = 5);
public record MlDeleteDocRequest(string DocumentId, string Collection);
public record MlDeleteDocResponse(int ChunksDeleted, bool Ok);
public record MlCollectionRequest(string Name);
public record MlHealthResponse(bool Ok, string Model, string Device);

public class MlClient(HttpClient http, ILogger<MlClient> logger)
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

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

    public async Task<List<ChunkResult>> SearchAsync(string query, string collection, int topK, CancellationToken ct = default)
    {
        var response = await http.PostAsJsonAsync("/ml/search", new MlSearchRequest(query, collection, topK), JsonOpts, ct);
        response.EnsureSuccessStatusCode();

        var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
        var results = new List<ChunkResult>();

        foreach (var item in result.GetProperty("results").EnumerateArray())
        {
            var text = item.GetProperty("text").GetString() ?? "";
            var score = item.GetProperty("score").GetDouble();
            var meta = new Dictionary<string, object>();
            foreach (var prop in item.GetProperty("metadata").EnumerateObject())
                meta[prop.Name] = prop.Value.ToString();
            results.Add(new ChunkResult(text, score, meta));
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
