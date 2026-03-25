namespace OpenRAG.Api.Models.Dto.Responses;

public record CollectionInfo(
    string Name,
    string Description,
    int DocumentCount,
    int ChunkCount
);

public record StatusResponse(string Status, string Message, object? Details = null);
