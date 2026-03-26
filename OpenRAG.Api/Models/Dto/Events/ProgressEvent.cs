namespace OpenRAG.Api.Models.Dto.Events;

/// <summary>Emitted over SignalR during document indexing.</summary>
/// <param name="Event">Always "progress".</param>
/// <param name="DocumentId">Document being indexed.</param>
/// <param name="Stage">converting | chunking | embedding | done | failed</param>
/// <param name="Progress">0–100</param>
public record ProgressEvent(string Event, string DocumentId, string Stage, int Progress);
