using System.Text.RegularExpressions;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class CollectionService(AppDbContext db, MlClient ml, ILogger<CollectionService> logger)
{
    private static readonly Regex ValidCollectionName = new(@"^[a-zA-Z0-9_-]{1,64}$", RegexOptions.Compiled);

    public async Task<List<CollectionInfo>> ListCollectionsAsync(CancellationToken ct = default)
    {
        return await db.Collections
            .Select(c => new CollectionInfo(
                c.Name,
                c.Description,
                c.Documents.Count(d => d.Status == "indexed"),
                c.Documents.Where(d => d.Status == "indexed").Sum(d => d.ChunkCount),
                c.ChunkSize,
                c.ChunkOverlap,
                c.SectionTokenThreshold,
                c.AutoDetectHeadings,
                c.HeadingScript))
            .ToListAsync(ct);
    }

    public async Task<StatusResponse> CreateCollectionAsync(string name, string description, CancellationToken ct = default)
    {
        if (!ValidCollectionName.IsMatch(name))
            return new StatusResponse("error", "Collection name must be 1-64 characters: letters, digits, _ or -");

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

    public async Task<StatusResponse> UpdateSettingsAsync(string name, CollectionSettingsRequest req, CancellationToken ct = default)
    {
        var col = await db.Collections.FirstOrDefaultAsync(c => c.Name == name, ct);
        if (col is null)
            return new StatusResponse("error", $"Collection '{name}' not found");

        if (req.ChunkSize.HasValue)
            col.ChunkSize = Math.Clamp(req.ChunkSize.Value, 100, 1000);
        if (req.ChunkOverlap.HasValue)
            col.ChunkOverlap = Math.Clamp(req.ChunkOverlap.Value, 0, 100);
        if (req.SectionTokenThreshold.HasValue)
            col.SectionTokenThreshold = Math.Clamp(req.SectionTokenThreshold.Value, 0, 2000);
        if (req.AutoDetectHeadings.HasValue)
            col.AutoDetectHeadings = req.AutoDetectHeadings.Value;
        if (req.HeadingScript is not null)
            col.HeadingScript = string.IsNullOrWhiteSpace(req.HeadingScript) ? null : req.HeadingScript;

        await db.SaveChangesAsync(ct);

        logger.LogInformation("Updated chunking settings for collection '{Name}'", name);
        return new StatusResponse("ok", $"Settings for '{name}' updated");
    }

    public async Task<Collection?> GetCollectionAsync(string name, CancellationToken ct = default)
    {
        return await db.Collections.FirstOrDefaultAsync(c => c.Name == name, ct);
    }
}
