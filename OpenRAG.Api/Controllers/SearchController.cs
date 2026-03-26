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

        List<ChunkResult> results;

        switch (req.SearchMode)
        {
            case "multi-query":
                results = await MultiQuerySearchAsync(req, ct);
                break;
            case "hyde":
                results = await HydeSearchAsync(req, ct);
                break;
            default:
                results = await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);
                break;
        }

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            return Ok(new SearchResponse(req.Query, results, results.Count, generated?.Answer, generated?.Citations));
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }

    private async Task<List<ChunkResult>> MultiQuerySearchAsync(SearchRequest req, CancellationToken ct)
    {
        // Generate alternative queries
        var altQueries = llm.IsEnabled
            ? await llm.GenerateQueriesAsync(req.Query, 3, ct)
            : null;

        var allQueries = new List<string> { req.Query };
        if (altQueries is not null)
            allQueries.AddRange(altQueries);

        // Search each query
        var allResults = new List<ChunkResult>();
        foreach (var q in allQueries)
        {
            var results = await ml.SearchAsync(q, req.Collection, req.TopK, req.UseReranker, "semantic", ct);
            allResults.AddRange(results);
        }

        // Deduplicate by text, keep highest score
        var deduped = allResults
            .GroupBy(r => r.Text)
            .Select(g => g.OrderByDescending(r => r.Score).First())
            .OrderByDescending(r => r.Score)
            .Take(req.TopK)
            .ToList();

        return deduped;
    }

    private async Task<List<ChunkResult>> HydeSearchAsync(SearchRequest req, CancellationToken ct)
    {
        // Generate hypothetical document
        var hypothetical = llm.IsEnabled
            ? await llm.GenerateHypotheticalAsync(req.Query, ct)
            : null;

        if (hypothetical is null)
        {
            // Fallback to normal search if LLM unavailable
            return await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, "semantic", ct);
        }

        // Search using hypothetical document embedding
        var results = await ml.SearchWithTextAsync(
            hypothetical, req.Collection, req.TopK, req.UseReranker, "semantic", ct);

        return results;
    }
}
