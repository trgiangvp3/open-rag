using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm, CollectionService collections) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Search([FromBody] SearchRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        var results = await RetrieveAsync(req.Query, req.Collection, req.TopK,
            req.UseReranker, req.SearchMode, req.QueryStrategy, ct);

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            return Ok(new SearchResponse(req.Query, results, results.Count, generated?.Answer, generated?.Citations));
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }

    internal async Task<List<ChunkResult>> RetrieveAsync(
        string query, string collection, int topK,
        bool useReranker, string searchMode, string queryStrategy,
        CancellationToken ct)
    {
        if (queryStrategy == "direct")
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct);

        // For LLM-based strategies: get context first
        var collectionDesc = await collections.GetDescriptionAsync(collection, ct);
        var prelimResults = await ml.SearchAsync(query, collection, 3, false, "semantic", ct);
        var sampleTexts = prelimResults.Select(r => r.Text).ToList();

        return queryStrategy switch
        {
            "multi-query" => await MultiQueryAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, ct),
            "hyde" => await HydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, ct),
            "multi-query+hyde" => await MultiQueryHydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, ct),
            _ => await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct),
        };
    }

    private async Task<List<ChunkResult>> MultiQueryAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts, CancellationToken ct)
    {
        var altQueries = await llm.GenerateQueriesAsync(query, 3, collectionDesc, sampleTexts, ct: ct);
        var allQueries = new List<string> { query };
        if (altQueries is not null) allQueries.AddRange(altQueries);

        var all = new List<ChunkResult>();
        foreach (var q in allQueries)
            all.AddRange(await ml.SearchAsync(q, collection, topK, useReranker, searchMode, ct));

        return Deduplicate(all, topK);
    }

    private async Task<List<ChunkResult>> HydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts, CancellationToken ct)
    {
        var hypothetical = await llm.GenerateHypotheticalAsync(query, collectionDesc, sampleTexts, ct: ct);
        if (hypothetical is null)
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct);

        var hydeResults = await ml.SearchWithTextAsync(hypothetical, collection, topK, useReranker, "semantic", ct);
        if (searchMode == "hybrid")
        {
            var bm25Results = await ml.SearchAsync(query, collection, topK, useReranker, "hybrid", ct);
            hydeResults.AddRange(bm25Results);
            return Deduplicate(hydeResults, topK);
        }
        return hydeResults;
    }

    private async Task<List<ChunkResult>> MultiQueryHydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts, CancellationToken ct)
    {
        var multiTask = MultiQueryAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, ct);
        var hydeTask = HydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, ct);
        await Task.WhenAll(multiTask, hydeTask);

        var all = new List<ChunkResult>();
        all.AddRange(multiTask.Result);
        all.AddRange(hydeTask.Result);
        return Deduplicate(all, topK);
    }

    private static List<ChunkResult> Deduplicate(List<ChunkResult> results, int topK)
    {
        return results
            .GroupBy(r => r.Text)
            .Select(g => g.OrderByDescending(r => r.Score).First())
            .OrderByDescending(r => r.Score)
            .Take(topK)
            .ToList();
    }
}
