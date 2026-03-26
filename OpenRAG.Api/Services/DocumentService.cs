using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Models.Dto.Events;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services.Chunking;

namespace OpenRAG.Api.Services;

public class DocumentService(
    AppDbContext db,
    MlClient ml,
    MarkdownChunker chunker,
    IHubContext<ProgressHub> hub,
    ILogger<DocumentService> logger)
{
    private Task ReportProgressAsync(string documentId, string stage, int progress) =>
        hub.Clients.All.SendAsync("progress",
            new ProgressEvent("progress", documentId, stage, progress));

    public async Task<IngestResponse> IngestFileAsync(
        Stream fileStream, string filename, string collection, long sizeBytes, CancellationToken ct = default)
    {
        var col = await GetOrCreateCollectionAsync(collection, ct);
        var documentId = Guid.NewGuid();
        var docIdStr = documentId.ToString();

        var doc = new Document
        {
            Id = documentId,
            Filename = filename,
            CollectionId = col.Id,
            SizeBytes = sizeBytes,
            Status = "indexing",
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            // Convert file → markdown
            _ = ReportProgressAsync(docIdStr, "converting", 10);
            var markdown = await ml.ConvertFileAsync(fileStream, filename, ct);

            // Chunk in .NET
            _ = ReportProgressAsync(docIdStr, "chunking", 35);
            var chunks = chunker.Chunk(markdown, new Dictionary<string, string> { ["filename"] = filename });
            logger.LogInformation("Chunked '{Filename}' into {Count} chunks", filename, chunks.Count);

            if (chunks.Count == 0)
            {
                doc.Status = "indexed";
                doc.ChunkCount = 0;
                doc.IndexedAt = DateTime.UtcNow;
                await db.SaveChangesAsync(ct);
                _ = ReportProgressAsync(docIdStr, "done", 100);
                return new IngestResponse(docIdStr, filename, 0, "No content extracted");
            }

            // Embed + store via ML service
            _ = ReportProgressAsync(docIdStr, "embedding", 55);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(
                new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _ = ReportProgressAsync(docIdStr, "done", 100);

            return new IngestResponse(docIdStr, filename, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest '{Filename}'", filename);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            _ = ReportProgressAsync(docIdStr, "failed", 0);
            throw;
        }
    }

    public async Task<IngestResponse> IngestTextAsync(
        string text, string title, string collection, CancellationToken ct = default)
    {
        var col = await GetOrCreateCollectionAsync(collection, ct);
        var documentId = Guid.NewGuid();
        var docIdStr = documentId.ToString();

        _ = ReportProgressAsync(docIdStr, "chunking", 35);
        var chunks = chunker.Chunk(text, new Dictionary<string, string> { ["filename"] = title });

        var doc = new Document
        {
            Id = documentId,
            Filename = title,
            CollectionId = col.Id,
            SizeBytes = System.Text.Encoding.UTF8.GetByteCount(text),
            Status = "indexing",
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            if (chunks.Count == 0)
            {
                doc.Status = "indexed";
                doc.ChunkCount = 0;
                doc.IndexedAt = DateTime.UtcNow;
                await db.SaveChangesAsync(ct);
                _ = ReportProgressAsync(docIdStr, "done", 100);
                return new IngestResponse(docIdStr, title, 0, "No content to index");
            }

            _ = ReportProgressAsync(docIdStr, "embedding", 55);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(
                new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            _ = ReportProgressAsync(docIdStr, "done", 100);

            return new IngestResponse(docIdStr, title, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest text '{Title}'", title);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            _ = ReportProgressAsync(docIdStr, "failed", 0);
            throw;
        }
    }

    public async Task<DocumentListResponse> ListDocumentsAsync(string collection, CancellationToken ct = default)
    {
        var docs = await db.Documents
            .Include(d => d.Collection)
            .Where(d => d.Collection.Name == collection && d.Status == "indexed")
            .OrderByDescending(d => d.CreatedAt)
            .Select(d => new DocumentInfo(
                d.Id.ToString(),
                d.Filename,
                d.Collection.Name,
                d.ChunkCount,
                d.CreatedAt.ToString("o")))
            .ToListAsync(ct);

        return new DocumentListResponse(docs, docs.Count);
    }

    public async Task<StatusResponse> DeleteDocumentAsync(Guid documentId, string collection, CancellationToken ct = default)
    {
        var doc = await db.Documents
            .Include(d => d.Collection)
            .FirstOrDefaultAsync(d => d.Id == documentId && d.Collection.Name == collection, ct);

        if (doc is null)
            return new StatusResponse("error", "Document not found");

        var deleted = await ml.DeleteDocumentAsync(documentId, collection, ct);
        db.Documents.Remove(doc);
        await db.SaveChangesAsync(ct);

        return new StatusResponse("ok", $"Deleted {deleted} chunks");
    }

    private async Task<Collection> GetOrCreateCollectionAsync(string name, CancellationToken ct)
    {
        var col = await db.Collections.FirstOrDefaultAsync(c => c.Name == name, ct);
        if (col is not null) return col;

        col = new Collection { Name = name };
        db.Collections.Add(col);
        await db.SaveChangesAsync(ct);
        await ml.EnsureCollectionAsync(name, ct);
        return col;
    }
}
