namespace OpenRAG.Api.Models.Dto.Requests;

public class CollectionSettingsRequest
{
    public int? ChunkSize { get; set; }
    public int? ChunkOverlap { get; set; }
    public int? SectionTokenThreshold { get; set; }
    public bool? AutoDetectHeadings { get; set; }
    public string? HeadingScript { get; set; }
}

public class TestHeadingScriptRequest
{
    public string Script { get; set; } = "";
    public string SampleText { get; set; } = "";
    public int? ChunkSize { get; set; }
    public int? ChunkOverlap { get; set; }
    public int? SectionTokenThreshold { get; set; }
    public bool? AutoDetectHeadings { get; set; }
}
