namespace OpenRAG.Api.Models.Dto.Responses;

public record DocumentInfo(
    string Id,
    string Filename,
    string Collection,
    int ChunkCount,
    string CreatedAt
);

public record DocumentListResponse(List<DocumentInfo> Documents, int Total);

public record IngestResponse(string DocumentId, string Filename, int ChunkCount, string Message);
