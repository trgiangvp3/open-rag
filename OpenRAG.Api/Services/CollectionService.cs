using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class CollectionService(AppDbContext db, MlClient ml, ILogger<CollectionService> logger)
{
    public async Task<List<CollectionInfo>> ListCollectionsAsync(CancellationToken ct = default)
    {
        return await db.Collections
            .Select(c => new CollectionInfo(
                c.Name,
                c.Description,
                c.Documents.Count(d => d.Status == "indexed"),
                c.Documents.Where(d => d.Status == "indexed").Sum(d => d.ChunkCount)))
            .ToListAsync(ct);
    }

    public async Task<StatusResponse> CreateCollectionAsync(string name, string description, CancellationToken ct = default)
    {
        if (await db.Collections.AnyAsync(c => c.Name == name, ct))
            return new StatusResponse("error", $"Collection '{name}' already exists");

        db.Collections.Add(new Collection { Name = name, Description = description });
        await db.SaveChangesAsync(ct);
        await ml.EnsureCollectionAsync(name, ct);

        logger.LogInformation("Created collection '{Name}'", name);
        return new StatusResponse("ok", $"Collection '{name}' created");
    }

    public async Task<StatusResponse> DeleteCollectionAsync(string name, CancellationToken ct = default)
    {
        if (name == "documents")
            return new StatusResponse("error", "Cannot delete default collection");

        var col = await db.Collections.FirstOrDefaultAsync(c => c.Name == name, ct);
        if (col is null)
            return new StatusResponse("error", $"Collection '{name}' not found");

        await ml.DeleteCollectionAsync(name, ct);
        db.Collections.Remove(col); // cascades to Documents
        await db.SaveChangesAsync(ct);

        logger.LogInformation("Deleted collection '{Name}'", name);
        return new StatusResponse("ok", $"Collection '{name}' deleted");
    }
}
