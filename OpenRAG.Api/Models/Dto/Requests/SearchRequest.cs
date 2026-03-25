namespace OpenRAG.Api.Models.Dto.Requests;

public record SearchRequest(string Query, string Collection = "documents", int TopK = 5);
