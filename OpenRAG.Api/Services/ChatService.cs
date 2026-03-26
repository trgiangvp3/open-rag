using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Dto.Shared;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class ChatService(AppDbContext db, MlClient ml, LlmClient llm, ILogger<ChatService> logger)
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

        // 2. Retrieve relevant chunks
        var chunks = await ml.SearchAsync(
            req.Query, req.Collection, req.TopK,
            req.UseReranker, req.SearchMode, ct);

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
}
