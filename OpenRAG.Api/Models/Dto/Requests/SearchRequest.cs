using System.ComponentModel.DataAnnotations;

namespace OpenRAG.Api.Models.Dto.Requests;

public record SearchRequest(
    [MaxLength(5000)] string Query,
    string Collection = "documents",
    [Range(1, 100)] int TopK = 5,
    bool UseReranker = false,
    string SearchMode = "hybrid",
    string QueryStrategy = "direct",
    bool Generate = false,
    // Metadata filters (hard, ChromaDB WHERE)
    string? DocumentType = null,
    string? DateFrom = null,
    string? DateTo = null,
    // Facet boost (soft, re-ranking)
    string? DomainSlug = null,
    string? Subject = null
);
