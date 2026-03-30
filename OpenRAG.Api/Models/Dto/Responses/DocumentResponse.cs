namespace OpenRAG.Api.Models.Dto.Responses;

public record DocumentInfo(
    string Id,
    string Filename,
    string Collection,
    int ChunkCount,
    string CreatedAt,
    string? DocumentType = null,
    string? DocumentTypeDisplay = null,
    string? DocumentNumber = null,
    string? DocumentTitle = null,
    string? IssuingAuthority = null,
    string? IssuedDate = null,
    string? Tags = null
);

public record DocumentListResponse(List<DocumentInfo> Documents, int Total);

public record IngestResponse(string DocumentId, string Filename, int ChunkCount, string Message);
