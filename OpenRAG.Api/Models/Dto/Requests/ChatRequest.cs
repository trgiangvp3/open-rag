using System.ComponentModel.DataAnnotations;

namespace OpenRAG.Api.Models.Dto.Requests;

public record ChatRequest(
    [MaxLength(5000)] string Query,
    string Collection = "documents",
    Guid? SessionId = null,
    [Range(1, 100)] int TopK = 5,
    bool UseReranker = false,
    string SearchMode = "semantic"
);
