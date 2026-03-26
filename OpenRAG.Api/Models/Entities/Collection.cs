namespace OpenRAG.Api.Models.Entities;

public class Collection
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;

    // Chunking configuration
    public int ChunkSize { get; set; } = 400;
    public int ChunkOverlap { get; set; } = 50;
    public int SectionTokenThreshold { get; set; } = 800;
    public bool AutoDetectHeadings { get; set; } = true;
    public string? HeadingScript { get; set; }

    public ICollection<Document> Documents { get; set; } = [];
}
