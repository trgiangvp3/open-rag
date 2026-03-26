using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
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
        return queryStrategy switch
        {
            "multi-query" => await MultiQueryAsync(query, collection, topK, useReranker, searchMode, ct),
            "hyde" => await HydeAsync(query, collection, topK, useReranker, searchMode, ct),
            "multi-query+hyde" => await MultiQueryHydeAsync(query, collection, topK, useReranker, searchMode, ct),
            _ => await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct),
        };
    }

    private async Task<List<ChunkResult>> MultiQueryAsync(
        string query, string collection, int topK, bool useReranker, string searchMode, CancellationToken ct)
    {
        var altQueries = llm.IsEnabled ? await llm.GenerateQueriesAsync(query, 3, ct) : null;
        var allQueries = new List<string> { query };
        if (altQueries is not null) allQueries.AddRange(altQueries);

        var all = new List<ChunkResult>();
        foreach (var q in allQueries)
            all.AddRange(await ml.SearchAsync(q, collection, topK, useReranker, searchMode, ct));

        return Deduplicate(all, topK);
    }

    private async Task<List<ChunkResult>> HydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode, CancellationToken ct)
    {
        var hypothetical = llm.IsEnabled ? await llm.GenerateHypotheticalAsync(query, ct) : null;
        if (hypothetical is null)
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct);

        // HyDE: embed hypothetical doc for semantic, but use original query for BM25 in hybrid
        var hydeResults = await ml.SearchWithTextAsync(hypothetical, collection, topK, useReranker, "semantic", ct);

        if (searchMode == "hybrid")
        {
            // Also run BM25 with original query and merge
            var bm25Results = await ml.SearchAsync(query, collection, topK, useReranker, "hybrid", ct);
            hydeResults.AddRange(bm25Results);
            return Deduplicate(hydeResults, topK);
        }

        return hydeResults;
    }

    private async Task<List<ChunkResult>> MultiQueryHydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode, CancellationToken ct)
    {
        // Run both strategies in parallel
        var multiTask = MultiQueryAsync(query, collection, topK, useReranker, searchMode, ct);
        var hydeTask = HydeAsync(query, collection, topK, useReranker, searchMode, ct);

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
