using OpenRAG.Api.Models.Dto.Shared;

namespace OpenRAG.Api.Models.Dto.Responses;

public record ChatResponse(
    Guid SessionId,
    string? Answer,
    List<int>? Citations,
    List<ChunkResult> Chunks
);

public record ChatHistoryResponse(Guid SessionId, List<ChatMessage> Messages);
