namespace OpenRAG.Api.Models.Dto.Responses;

public record ChunkResult(string Text, double Score, Dictionary<string, object> Metadata);

public record SearchResponse(string Query, List<ChunkResult> Results, int Total);
