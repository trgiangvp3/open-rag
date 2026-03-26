namespace OpenRAG.Api.Models.Dto.Requests;

public record ChatRequest(
    string Query,
    string Collection = "documents",
    Guid? SessionId = null,
    int TopK = 5,
    bool UseReranker = false,
    string SearchMode = "semantic"
);
