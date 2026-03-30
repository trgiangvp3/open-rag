namespace OpenRAG.Api.Services.Chunking;

public interface IChunker
{
    List<Chunk> Chunk(string text, Dictionary<string, string>? metadata = null);
}
