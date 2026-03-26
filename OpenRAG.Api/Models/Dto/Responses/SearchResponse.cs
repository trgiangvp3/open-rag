namespace OpenRAG.Api.Models.Dto.Responses;

public record ChunkResult(string Text, double Score, Dictionary<string, object> Metadata, double? RerankScore = null);

public record SearchResponse(string Query, List<ChunkResult> Results, int Total, string? Answer = null, List<int>? Citations = null);
