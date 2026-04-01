using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Hubs;
using OpenRAG.Api.Models.Dto.Events;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services.Chunking;
using OpenRAG.Api.Services.Parsing;

namespace OpenRAG.Api.Services;

public class DocumentService(
    AppDbContext db,
    MlClient ml,
    IHubContext<ProgressHub> hub,
    ILogger<DocumentService> logger)
{

    private void ReportProgress(string documentId, string stage, int progress, string? filename = null) =>
        hub.Clients.All.SendAsync("progress",
            new ProgressEvent("progress", documentId, stage, progress, filename))
            .ContinueWith(
                t => logger.LogWarning(t.Exception, "Failed to send progress event for {DocumentId}", documentId),
                TaskContinuationOptions.OnlyOnFaulted);

    private static MarkdownChunker CreateChunker(Collection col)
    {
        return new MarkdownChunker(
            chunkSize: col.ChunkSize,
            chunkOverlap: col.ChunkOverlap,
            sectionTokenThreshold: col.SectionTokenThreshold,
            autoDetectHeadings: col.AutoDetectHeadings,
            headingScript: col.HeadingScript);
    }

    public async Task<IngestResponse> IngestFileAsync(
        Stream fileStream, string filename, string collection, long sizeBytes,
        string? tags = null, CancellationToken ct = default)
    {
        var ext = Path.GetExtension(filename).ToLowerInvariant();
        var isHtml = ext is ".html" or ".htm";

        // Compute content hash for dedup
        var hash = await ComputeHashAsync(fileStream, ct);
        fileStream.Position = 0;

        var col = await GetOrCreateCollectionAsync(collection, ct);
        var existing = await db.Documents
            .FirstOrDefaultAsync(d => d.ContentHash == hash && d.CollectionId == col.Id, ct);
        if (existing is not null)
            return new IngestResponse(existing.Id.ToString(), filename, existing.ChunkCount,
                $"Duplicate: identical content already indexed as '{existing.Filename}'");

        // For HTML files, read content to check if it's a legal document
        string? htmlContent = null;
        if (isHtml)
        {
            using var reader = new StreamReader(fileStream, leaveOpen: true);
            htmlContent = await reader.ReadToEndAsync(ct);
            fileStream.Position = 0; // Reset for potential ML service use
        }

        var isLegal = htmlContent is not null && LegalHtmlParser.IsLegalHtml(htmlContent);

        if (isLegal)
            return await IngestLegalHtmlAsync(htmlContent!, filename, collection, sizeBytes, tags, ct, hash);

        return await IngestGenericFileAsync(fileStream, filename, collection, sizeBytes, tags, ct, hash);
    }

    private static async Task<string> ComputeHashAsync(Stream stream, CancellationToken ct)
    {
        using var sha256 = SHA256.Create();
        var hashBytes = await sha256.ComputeHashAsync(stream, ct);
        return Convert.ToHexString(hashBytes).ToLowerInvariant();
    }

    /// <summary>Legal HTML pipeline — parse + chunk entirely in C#, only send to ML for embedding.</summary>
    private async Task<IngestResponse> IngestLegalHtmlAsync(
        string html, string filename, string collection, long sizeBytes,
        string? tags, CancellationToken ct, string? contentHash = null)
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
            ContentHash = contentHash,
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            // 1. Parse HTML for metadata + structure
            ReportProgress(docIdStr, "parsing", 10, filename);
            var legalMeta = LegalHtmlParser.TryParse(html);
            if (legalMeta is null)
            {
                // Fallback: treat as regular markdown
                logger.LogWarning("Legal HTML detection passed but parser failed for '{Filename}', falling back", filename);
                var stream = new MemoryStream(System.Text.Encoding.UTF8.GetBytes(html));
                return await IngestGenericFileInternalAsync(doc, stream, filename, collection, col, tags, ct);
            }

            // 2. Store metadata on document entity
            doc.DocumentType = legalMeta.DocumentType;
            doc.DocumentTypeDisplay = legalMeta.DocumentTypeDisplay;
            doc.DocumentNumber = legalMeta.DocumentNumber;
            doc.DocumentTitle = legalMeta.DocumentTitle;
            doc.IssuingAuthority = legalMeta.IssuingAuthority;
            doc.SignedLocation = legalMeta.SignedLocation;
            doc.IssuedDate = legalMeta.IssuedDate;
            doc.LegalBasisJson = legalMeta.LegalBases.Count > 0
                ? JsonSerializer.Serialize(legalMeta.LegalBases) : null;
            doc.TerminologyJson = legalMeta.Terminology.Count > 0
                ? JsonSerializer.Serialize(legalMeta.Terminology) : null;
            doc.ReferencedDocsJson = legalMeta.ReferencedDocs.Count > 0
                ? JsonSerializer.Serialize(legalMeta.ReferencedDocs) : null;

            doc.Tags = tags;
            doc.SubjectsJson = legalMeta.Subjects.Count > 0
                ? JsonSerializer.Serialize(legalMeta.Subjects) : null;
            doc.SuggestedDomainsJson = legalMeta.SuggestedDomains.Count > 0
                ? JsonSerializer.Serialize(legalMeta.SuggestedDomains) : null;

            // Auto-assign domain if top suggestion has high confidence
            if (legalMeta.SuggestedDomains.Count > 0)
            {
                var top = legalMeta.SuggestedDomains[0];
                var domain = await db.Domains.FirstOrDefaultAsync(d => d.Slug == top.Slug, ct);
                if (domain is not null && top.Confidence >= 0.7f)
                    doc.DomainId = domain.Id;
            }

            doc.MarkdownContent = legalMeta.PlainText;

            logger.LogInformation(
                "Parsed legal doc '{Filename}': type={Type}, number={Number}, domain={Domain}, subjects=[{Subjects}]",
                filename, legalMeta.DocumentTypeDisplay, legalMeta.DocumentNumber,
                legalMeta.SuggestedDomains.FirstOrDefault()?.Name,
                string.Join(", ", legalMeta.Subjects.Take(3)));

            // 3. Chunk by legal structure
            ReportProgress(docIdStr, "chunking", 35, filename);
            var chunker = new LegalDocumentChunker(legalMeta, chunkSize: col.ChunkSize, chunkOverlap: col.ChunkOverlap);
            var baseMeta = new Dictionary<string, string> { ["filename"] = filename };
            if (!string.IsNullOrEmpty(doc.Tags))
                baseMeta["tags"] = doc.Tags;
            if (doc.DomainId.HasValue)
            {
                var domainEntity = await db.Domains.Include(d => d.Parent).FirstOrDefaultAsync(d => d.Id == doc.DomainId, ct);
                if (domainEntity is not null)
                {
                    baseMeta["domain"] = domainEntity.Slug;
                    if (domainEntity.Parent is not null)
                        baseMeta["domain_parent"] = domainEntity.Parent.Slug;
                }
            }
            if (legalMeta.Subjects.Count > 0)
                baseMeta["subjects"] = string.Join(",", legalMeta.Subjects);
            var chunks = chunker.Chunk("", baseMeta);
            logger.LogInformation("Chunked legal doc '{Filename}' into {Count} chunks", filename, chunks.Count);

            if (chunks.Count == 0)
            {
                doc.Status = "indexed";
                doc.ChunkCount = 0;
                doc.IndexedAt = DateTime.UtcNow;
                await db.SaveChangesAsync(ct);
                ReportProgress(docIdStr, "done", 100, filename);
                return new IngestResponse(docIdStr, filename, 0, "No content extracted");
            }

            // 4. Embed + store via ML service
            ReportProgress(docIdStr, "embedding", 55, filename);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(
                new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "done", 100, filename);

            return new IngestResponse(docIdStr, filename, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks (legal: {legalMeta.DocumentTypeDisplay} {legalMeta.DocumentNumber})");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest legal HTML '{Filename}'", filename);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "failed", 0, filename);
            throw;
        }
    }

    /// <summary>Generic file pipeline — convert via ML service, then chunk markdown.</summary>
    private async Task<IngestResponse> IngestGenericFileAsync(
        Stream fileStream, string filename, string collection, long sizeBytes,
        string? tags, CancellationToken ct, string? contentHash = null)
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
            Tags = tags,
            ContentHash = contentHash,
        };
        db.Documents.Add(doc);
        await db.SaveChangesAsync(ct);

        try
        {
            return await IngestGenericFileInternalAsync(doc, fileStream, filename, collection, col, tags, ct);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest '{Filename}'", filename);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "failed", 0, filename);
            throw;
        }
    }

    private async Task<IngestResponse> IngestGenericFileInternalAsync(
        Document doc, Stream fileStream, string filename, string collection,
        Collection col, string? tags, CancellationToken ct)
    {
        var docIdStr = doc.Id.ToString();
        var chunker = CreateChunker(col);

        // Convert file -> markdown
        ReportProgress(docIdStr, "converting", 10, filename);
        var markdown = await ml.ConvertFileAsync(fileStream, filename, ct);
        doc.MarkdownContent = markdown;

        // Chunk in .NET
        ReportProgress(docIdStr, "chunking", 35, filename);
        var baseMeta = new Dictionary<string, string> { ["filename"] = filename };
        if (!string.IsNullOrEmpty(tags))
            baseMeta["tags"] = tags;
        var chunks = chunker.Chunk(markdown, baseMeta);
        logger.LogInformation("Chunked '{Filename}' into {Count} chunks", filename, chunks.Count);

        if (chunks.Count == 0)
        {
            doc.Status = "indexed";
            doc.ChunkCount = 0;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "done", 100, filename);
            return new IngestResponse(docIdStr, filename, 0, "No content extracted");
        }

        // Embed + store via ML service
        ReportProgress(docIdStr, "embedding", 55, filename);
        var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
        var result = await ml.IndexChunksAsync(
            new MlIndexRequest(docIdStr, collection, mlChunks), ct);

        doc.Status = "indexed";
        doc.ChunkCount = result.ChunkCount;
        doc.IndexedAt = DateTime.UtcNow;
        await db.SaveChangesAsync(ct);
        ReportProgress(docIdStr, "done", 100, filename);

        return new IngestResponse(docIdStr, filename, result.ChunkCount,
            $"Indexed {result.ChunkCount} chunks");
    }

    public async Task<IngestResponse> IngestTextAsync(
        string text, string title, string collection, CancellationToken ct = default)
    {
        var col = await GetOrCreateCollectionAsync(collection, ct);
        var chunker = CreateChunker(col);
        var documentId = Guid.NewGuid();
        var docIdStr = documentId.ToString();

        ReportProgress(docIdStr, "chunking", 35, title);
        var chunks = chunker.Chunk(text, new Dictionary<string, string> { ["filename"] = title });

        var doc = new Document
        {
            Id = documentId,
            Filename = title,
            CollectionId = col.Id,
            SizeBytes = System.Text.Encoding.UTF8.GetByteCount(text),
            Status = "indexing",
            MarkdownContent = text,
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
                ReportProgress(docIdStr, "done", 100, title);
                return new IngestResponse(docIdStr, title, 0, "No content to index");
            }

            ReportProgress(docIdStr, "embedding", 55, title);
            var mlChunks = chunks.Select(c => new MlChunkInput(c.Text, c.Metadata)).ToList();
            var result = await ml.IndexChunksAsync(
                new MlIndexRequest(docIdStr, collection, mlChunks), ct);

            doc.Status = "indexed";
            doc.ChunkCount = result.ChunkCount;
            doc.IndexedAt = DateTime.UtcNow;
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "done", 100, title);

            return new IngestResponse(docIdStr, title, result.ChunkCount,
                $"Indexed {result.ChunkCount} chunks");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Failed to ingest text '{Title}'", title);
            doc.Status = "failed";
            await db.SaveChangesAsync(ct);
            ReportProgress(docIdStr, "failed", 0, title);
            throw;
        }
    }

    public async Task<DocumentListResponse> ListDocumentsAsync(string collection, CancellationToken ct = default)
    {
        var docs = await db.Documents
            .Include(d => d.Collection)
            .Where(d => d.Collection.Name == collection)
            .OrderByDescending(d => d.Status == "indexing" ? 1 : 0) // indexing first
            .ThenByDescending(d => d.CreatedAt)
            .Select(d => new DocumentInfo(
                d.Id.ToString(),
                d.Filename,
                d.Collection.Name,
                d.ChunkCount,
                d.CreatedAt.ToString("o"),
                d.DocumentType,
                d.DocumentTypeDisplay,
                d.DocumentNumber,
                d.DocumentTitle,
                d.IssuingAuthority,
                d.IssuedDate.HasValue ? d.IssuedDate.Value.ToString("yyyy-MM-dd") : null,
                d.Tags,
                d.Status))
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

    public async Task<object?> GetDocumentMarkdownAsync(Guid documentId, CancellationToken ct = default)
    {
        var doc = await db.Documents.FirstOrDefaultAsync(d => d.Id == documentId, ct);
        if (doc is null) return null;
        return new { documentId = doc.Id.ToString(), filename = doc.Filename, markdown = doc.MarkdownContent ?? "" };
    }

    public async Task<object> GetDocumentChunksAsync(Guid documentId, string collection, CancellationToken ct = default)
    {
        return await ml.GetDocumentChunksAsync(documentId, collection, ct);
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

    private static int CountArticles(List<LegalSection> sections)
    {
        var count = 0;
        foreach (var s in sections)
        {
            if (s.Type == "article") count++;
            count += CountArticles(s.Children);
        }
        return count;
    }
}
