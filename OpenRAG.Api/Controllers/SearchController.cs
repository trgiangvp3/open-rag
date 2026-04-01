using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[Authorize]
[ApiController]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm, CollectionService collections, IHubContext<ProgressHub> hub) : ControllerBase
{
    private Task EmitStatus(string status) =>
        hub.Clients.All.SendAsync("search-status", new { status });

    [HttpPost]
    public async Task<IActionResult> Search([FromBody] SearchRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        var metadataFilter = BuildMetadataFilter(req);
        var hasFacetBoost = !string.IsNullOrEmpty(req.DomainSlug) || !string.IsNullOrEmpty(req.Subject);
        var fetchK = hasFacetBoost ? req.TopK * 3 : req.TopK;

        await EmitStatus("Tìm kiếm tài liệu liên quan...");
        var results = await RetrieveAsync(req.Query, req.Collection, fetchK,
            req.UseReranker, req.SearchMode, req.QueryStrategy, metadataFilter, ct);

        if (hasFacetBoost)
            results = ApplyFacetBoost(results, req.DomainSlug, req.Subject, req.TopK);

        if (req.ScoreThreshold.HasValue)
            results = results.Where(r => (r.RerankScore ?? r.Score) >= req.ScoreThreshold.Value).ToList();

        if (req.Generate && llm.IsEnabled)
        {
            await EmitStatus("Sinh câu trả lời...");
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            await EmitStatus("");
            return Ok(new SearchResponse(req.Query, results, results.Count, generated?.Answer, generated?.Citations));
        }

        await EmitStatus("");
        return Ok(new SearchResponse(req.Query, results, results.Count));
    }

    internal async Task<List<ChunkResult>> RetrieveAsync(
        string query, string collection, int topK,
        bool useReranker, string searchMode, string queryStrategy,
        Dictionary<string, object>? metadataFilter,
        CancellationToken ct)
    {
        if (queryStrategy == "direct")
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, metadataFilter, ct);

        // For LLM-based strategies: get context first
        await EmitStatus("Thu thập ngữ cảnh từ tài liệu...");
        var collectionDesc = await collections.GetDescriptionAsync(collection, ct);
        var prelimResults = await ml.SearchAsync(query, collection, 3, false, "semantic", metadataFilter, ct);
        var sampleTexts = prelimResults.Select(r => r.Text).ToList();

        return queryStrategy switch
        {
            "multi-query" => await MultiQueryAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, metadataFilter, ct),
            "hyde" => await HydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, metadataFilter, ct),
            "multi-query+hyde" => await MultiQueryHydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, metadataFilter, ct),
            _ => await ml.SearchAsync(query, collection, topK, useReranker, searchMode, metadataFilter, ct),
        };
    }

    private async Task<List<ChunkResult>> MultiQueryAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts,
        Dictionary<string, object>? metadataFilter, CancellationToken ct)
    {
        await EmitStatus("Sinh biến thể câu hỏi (LLM)...");
        var altQueries = await llm.GenerateQueriesAsync(query, 3, collectionDesc, sampleTexts, ct: ct);
        var allQueries = new List<string> { query };
        if (altQueries is not null) allQueries.AddRange(altQueries);

        await EmitStatus($"Tìm kiếm {allQueries.Count} queries...");
        var all = new List<ChunkResult>();
        foreach (var q in allQueries)
            all.AddRange(await ml.SearchAsync(q, collection, topK, useReranker, searchMode, metadataFilter, ct));

        await EmitStatus("Tổng hợp kết quả...");
        return Deduplicate(all, topK);
    }

    private async Task<List<ChunkResult>> HydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts,
        Dictionary<string, object>? metadataFilter, CancellationToken ct)
    {
        await EmitStatus("Sinh tài liệu tham chiếu (HyDE)...");
        var hypothetical = await llm.GenerateHypotheticalAsync(query, collectionDesc, sampleTexts, ct: ct);
        if (hypothetical is null)
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, metadataFilter, ct);

        await EmitStatus("Tìm kiếm bằng embedding giả...");
        var hydeResults = await ml.SearchWithTextAsync(hypothetical, collection, topK, useReranker, "semantic", ct);
        if (searchMode == "hybrid")
        {
            var bm25Results = await ml.SearchAsync(query, collection, topK, useReranker, "hybrid", metadataFilter, ct);
            hydeResults.AddRange(bm25Results);
            return Deduplicate(hydeResults, topK);
        }
        return hydeResults;
    }

    private async Task<List<ChunkResult>> MultiQueryHydeAsync(
        string query, string collection, int topK, bool useReranker, string searchMode,
        string? collectionDesc, List<string> sampleTexts,
        Dictionary<string, object>? metadataFilter, CancellationToken ct)
    {
        await EmitStatus("Sinh queries + tài liệu tham chiếu (LLM)...");
        var multiTask = MultiQueryAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, metadataFilter, ct);
        var hydeTask = HydeAsync(query, collection, topK, useReranker, searchMode, collectionDesc, sampleTexts, metadataFilter, ct);
        await Task.WhenAll(multiTask, hydeTask);

        await EmitStatus("Tổng hợp kết quả đa chiều...");
        var all = new List<ChunkResult>();
        all.AddRange(multiTask.Result);
        all.AddRange(hydeTask.Result);
        return Deduplicate(all, topK);
    }

    private static Dictionary<string, object>? BuildMetadataFilter(SearchRequest req)
    {
        var conditions = new List<Dictionary<string, object>>();

        if (!string.IsNullOrEmpty(req.DocumentType))
            conditions.Add(new() { ["document_type"] = new Dictionary<string, object> { ["$eq"] = req.DocumentType } });

        if (!string.IsNullOrEmpty(req.DateFrom))
            conditions.Add(new() { ["issue_date"] = new Dictionary<string, object> { ["$gte"] = req.DateFrom } });

        if (!string.IsNullOrEmpty(req.DateTo))
            conditions.Add(new() { ["issue_date"] = new Dictionary<string, object> { ["$lte"] = req.DateTo } });

        if (!string.IsNullOrEmpty(req.Tags))
        {
            var tagList = req.Tags.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
            foreach (var tag in tagList)
                conditions.Add(new() { ["tags"] = new Dictionary<string, object> { ["$contains"] = tag } });
        }

        if (conditions.Count == 0) return null;
        if (conditions.Count == 1) return conditions[0];
        return new Dictionary<string, object> { ["$and"] = conditions };
    }

    /// <summary>
    /// Soft boost: re-score results based on domain/subject match, then take topK.
    /// Does NOT filter out non-matching results — only adjusts ranking.
    /// </summary>
    private static List<ChunkResult> ApplyFacetBoost(
        List<ChunkResult> results, string? domainSlug, string? subject, int topK)
    {
        const double DomainL2Boost = 0.15;
        const double DomainL1Boost = 0.08;
        const double SubjectBoost = 0.07;

        var boosted = results.Select(r =>
        {
            var boost = 0.0;

            if (!string.IsNullOrEmpty(domainSlug))
            {
                var chunkDomain = r.Metadata.GetValueOrDefault("domain")?.ToString() ?? "";
                var chunkDomainParent = r.Metadata.GetValueOrDefault("domain_parent")?.ToString() ?? "";

                if (chunkDomain.Equals(domainSlug, StringComparison.OrdinalIgnoreCase))
                    boost += DomainL2Boost;
                else if (chunkDomainParent.Equals(domainSlug, StringComparison.OrdinalIgnoreCase))
                    boost += DomainL1Boost;
            }

            if (!string.IsNullOrEmpty(subject))
            {
                var chunkSubjects = r.Metadata.GetValueOrDefault("subjects")?.ToString() ?? "";
                if (chunkSubjects.Contains(subject, StringComparison.OrdinalIgnoreCase))
                    boost += SubjectBoost;
            }

            return new ChunkResult(r.Text, r.Score + boost, r.Metadata, r.RerankScore);
        });

        return boosted.OrderByDescending(r => r.Score).Take(topK).ToList();
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
