using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Dto.Shared;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class ChatService(AppDbContext db, MlClient ml, LlmClient llm, CollectionService collections, ILogger<ChatService> logger)
{
    public async Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
    {
        // 1. Get or create session
        ChatSession session;
        if (req.SessionId.HasValue)
        {
            session = await db.ChatSessions
                .Include(s => s.Messages)
                .FirstOrDefaultAsync(s => s.Id == req.SessionId.Value, ct)
                ?? throw new KeyNotFoundException($"Session {req.SessionId} not found");
        }
        else
        {
            session = new ChatSession { Collection = req.Collection };
            db.ChatSessions.Add(session);
            await db.SaveChangesAsync(ct);
        }

        // 2. Retrieve relevant chunks (supports multi-query, HyDE, multi+hyde)
        var chunks = await RetrieveAsync(req.Query, req.Collection, req.TopK,
            req.UseReranker, req.SearchMode, req.QueryStrategy, ct);

        // 3. Build conversation history for LLM
        var history = session.Messages
            .OrderBy(m => m.CreatedAt)
            .Select(m => new ChatMessage(m.Role, m.Content))
            .ToList();

        // 4. Generate answer (optional, requires LLM to be configured)
        GenerateResult? generated = null;
        if (llm.IsEnabled)
            generated = await llm.GenerateAsync(req.Query, chunks, history, ct);

        // 5. Persist this turn
        db.ChatMessages.Add(new ChatMessageEntity
        {
            SessionId = session.Id,
            Role = "user",
            Content = req.Query,
        });

        if (generated is not null)
            db.ChatMessages.Add(new ChatMessageEntity
            {
                SessionId = session.Id,
                Role = "assistant",
                Content = generated.Answer,
            });

        session.UpdatedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);

        logger.LogInformation("Chat session {SessionId}: query processed, {ChunkCount} chunks retrieved",
            session.Id, chunks.Count);

        return new ChatResponse(session.Id, generated?.Answer, generated?.Citations, chunks);
    }

    public async Task<ChatHistoryResponse?> GetHistoryAsync(Guid sessionId, CancellationToken ct = default)
    {
        var session = await db.ChatSessions
            .Include(s => s.Messages)
            .FirstOrDefaultAsync(s => s.Id == sessionId, ct);

        if (session is null) return null;

        var messages = session.Messages
            .OrderBy(m => m.CreatedAt)
            .Select(m => new ChatMessage(m.Role, m.Content))
            .ToList();

        return new ChatHistoryResponse(session.Id, messages);
    }

    public async Task DeleteSessionAsync(Guid sessionId, CancellationToken ct = default)
    {
        var session = await db.ChatSessions.FindAsync([sessionId], ct);
        if (session is not null)
        {
            db.ChatSessions.Remove(session);
            await db.SaveChangesAsync(ct);
        }
    }

    private async Task<List<ChunkResult>> RetrieveAsync(
        string query, string collection, int topK,
        bool useReranker, string searchMode, string queryStrategy, CancellationToken ct)
    {
        if (queryStrategy == "direct")
            return await ml.SearchAsync(query, collection, topK, useReranker, searchMode, ct);

        // Get context for LLM-based strategies
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
