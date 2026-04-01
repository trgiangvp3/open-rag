namespace OpenRAG.Api.Models.Entities;

public class Document
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Filename { get; set; } = "";
    public int CollectionId { get; set; }
    public Collection Collection { get; set; } = null!;
    public int ChunkCount { get; set; }
    public long SizeBytes { get; set; }
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime? IndexedAt { get; set; }
    public string Status { get; set; } = "indexing"; // indexing | indexed | failed
    public string? MarkdownContent { get; set; }

    // Legal document metadata (VBQPPL)
    public string? DocumentType { get; set; }        // "thong_tu", "nghi_dinh", "luat", ...
    public string? DocumentTypeDisplay { get; set; }  // "Thông tư", "Nghị định", "Luật", ...
    public string? DocumentNumber { get; set; }       // "72/2025/TT-NHNN"
    public string? DocumentTitle { get; set; }        // Full legal title
    public string? IssuingAuthority { get; set; }     // "Ngân hàng Nhà nước Việt Nam"
    public string? SignedLocation { get; set; }       // "Hà Nội"
    public DateTime? IssuedDate { get; set; }
    public DateTime? EffectiveDate { get; set; }
    public string? LegalBasisJson { get; set; }       // JSON: [{text, number?, url?}]
    public string? TerminologyJson { get; set; }      // JSON: [{term, definition}]
    public string? ReferencedDocsJson { get; set; }   // JSON: [{number, title?, url}]
    public string? ContentHash { get; set; }            // SHA256 hash for deduplication
    public string? Tags { get; set; }                 // Comma-separated manual tags

    // Faceted classification
    public int? DomainId { get; set; }                // FK → Domains (confirmed by user)
    public Domain? Domain { get; set; }
    public string? SuggestedDomainsJson { get; set; } // JSON: [{id, name, source, confidence}]
    public string? SubjectsJson { get; set; }         // JSON: ["Ngân hàng thương mại", ...]
}
