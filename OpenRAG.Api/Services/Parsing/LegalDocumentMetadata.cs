namespace OpenRAG.Api.Services.Parsing;

/// <summary>Parsed metadata from a Vietnamese legal document (VBQPPL).</summary>
public record LegalDocumentMetadata(
    string DocumentType,
    string DocumentTypeDisplay,
    string? DocumentNumber,
    string? DocumentTitle,
    string? IssuingAuthority,
    string? SignedLocation,
    DateTime? IssuedDate,
    List<LegalBasis> LegalBases,
    List<TermDefinition> Terminology,
    List<ReferencedDoc> ReferencedDocs,
    List<LegalSection> Sections,
    string PlainText,
    List<string> Subjects,
    List<DomainSuggestion> SuggestedDomains);

public record DomainSuggestion(string Name, string Slug, string Source, float Confidence);

public record LegalBasis(string Text, string? Number, string? Url);

public record TermDefinition(string Term, string Definition);

public record ReferencedDoc(string Number, string? Title, string Url);

public record LegalSection(
    string Type,
    string Number,
    string Title,
    string Content,
    string Path,
    List<LegalSection> Children);
