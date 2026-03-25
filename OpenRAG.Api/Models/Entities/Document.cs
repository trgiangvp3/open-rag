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
}
