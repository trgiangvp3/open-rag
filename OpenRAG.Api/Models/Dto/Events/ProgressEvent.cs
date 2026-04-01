namespace OpenRAG.Api.Models.Dto.Events;

/// <summary>Emitted over SignalR during document indexing.</summary>
/// <param name="Event">Always "progress".</param>
/// <param name="DocumentId">Document being indexed.</param>
/// <param name="Stage">converting | parsing | chunking | embedding | done | failed</param>
/// <param name="Progress">0–100</param>
/// <param name="Filename">Original filename for matching before documentId is known.</param>
public record ProgressEvent(string Event, string DocumentId, string Stage, int Progress, string? Filename = null);
