namespace OpenRAG.Api.Models.Dto.Responses;

public record CollectionInfo(
    string Name,
    string Description,
    int DocumentCount,
    int ChunkCount,
    int ChunkSize = 400,
    int ChunkOverlap = 50,
    int SectionTokenThreshold = 800,
    bool AutoDetectHeadings = true,
    string? HeadingScript = null
);

public record StatusResponse(string Status, string Message, object? Details = null);
