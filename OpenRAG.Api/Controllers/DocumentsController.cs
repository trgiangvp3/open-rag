using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/documents")]
public class DocumentsController(DocumentService docs, AppDbContext db) : ControllerBase
{
    private const long MaxFileSizeBytes = 100 * 1024 * 1024; // 100 MB
    private const int MaxTextLength = 10 * 1024 * 1024;      // 10 MB
    private static readonly HashSet<string> AllowedExtensions =
        [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md", ".html", ".htm", ".csv"];

    [HttpPost("upload")]
    public async Task<IActionResult> Upload(
        IFormFile file,
        [FromForm] string collection = "documents",
        [FromForm] string? tags = null,
        CancellationToken ct = default)
    {
        if (file is null || file.Length == 0)
            return BadRequest(new { detail = "No file provided" });

        if (file.Length > MaxFileSizeBytes)
            return BadRequest(new { detail = $"File exceeds {MaxFileSizeBytes / 1024 / 1024} MB limit" });

        var ext = Path.GetExtension(file.FileName).ToLowerInvariant();
        if (!AllowedExtensions.Contains(ext))
            return BadRequest(new { detail = $"File type '{ext}' not allowed" });

        var safeFilename = Path.GetFileName(file.FileName);
        await using var stream = file.OpenReadStream();
        var result = await docs.IngestFileAsync(stream, safeFilename, collection, file.Length, tags, ct);
        return Ok(result);
    }

    [HttpPost("text")]
    public async Task<IActionResult> IngestText(
        [FromForm] string text,
        [FromForm] string title = "untitled",
        [FromForm] string collection = "documents",
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(text))
            return BadRequest(new { detail = "No text provided" });

        if (text.Length > MaxTextLength)
            return BadRequest(new { detail = $"Text exceeds {MaxTextLength / 1024 / 1024} MB limit" });

        var result = await docs.IngestTextAsync(text, title, collection, ct);
        return Ok(result);
    }

    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] string collection = "documents",
        CancellationToken ct = default)
    {
        var result = await docs.ListDocumentsAsync(collection, ct);
        return Ok(result);
    }

    [HttpGet("{documentId}/markdown")]
    public async Task<IActionResult> GetMarkdown(
        Guid documentId,
        CancellationToken ct = default)
    {
        var result = await docs.GetDocumentMarkdownAsync(documentId, ct);
        if (result is null) return NotFound(new { detail = "Document not found" });
        return Ok(result);
    }

    [HttpGet("{documentId}/chunks")]
    public async Task<IActionResult> GetChunks(
        Guid documentId,
        [FromQuery] string collection = "documents",
        CancellationToken ct = default)
    {
        var result = await docs.GetDocumentChunksAsync(documentId, collection, ct);
        return Ok(result);
    }

    [HttpDelete("{documentId}")]
    public async Task<IActionResult> Delete(
        Guid documentId,
        [FromQuery] string collection = "documents",
        CancellationToken ct = default)
    {
        var result = await docs.DeleteDocumentAsync(documentId, collection, ct);
        if (result.Status == "error")
            return NotFound(result);
        return Ok(result);
    }

    [HttpGet("{documentId}/metadata")]
    public async Task<IActionResult> GetMetadata(Guid documentId, CancellationToken ct = default)
    {
        var doc = await db.Documents.FirstOrDefaultAsync(d => d.Id == documentId, ct);
        if (doc is null) return NotFound(new { detail = "Document not found" });

        return Ok(new
        {
            documentId = doc.Id.ToString(),
            filename = doc.Filename,
            documentType = doc.DocumentType,
            documentTypeDisplay = doc.DocumentTypeDisplay,
            documentNumber = doc.DocumentNumber,
            documentTitle = doc.DocumentTitle,
            issuingAuthority = doc.IssuingAuthority,
            signedLocation = doc.SignedLocation,
            issuedDate = doc.IssuedDate?.ToString("yyyy-MM-dd"),
            effectiveDate = doc.EffectiveDate?.ToString("yyyy-MM-dd"),
            legalBasis = doc.LegalBasisJson,
            terminology = doc.TerminologyJson,
            referencedDocs = doc.ReferencedDocsJson,
            tags = doc.Tags,
            domainId = doc.DomainId,
            suggestedDomains = doc.SuggestedDomainsJson,
            subjects = doc.SubjectsJson,
        });
    }

    [HttpPut("{documentId}/domain")]
    public async Task<IActionResult> SetDomain(
        Guid documentId, [FromBody] SetDomainRequest req, CancellationToken ct = default)
    {
        var doc = await db.Documents.FirstOrDefaultAsync(d => d.Id == documentId, ct);
        if (doc is null) return NotFound(new { detail = "Document not found" });

        if (req.DomainId.HasValue)
        {
            var domain = await db.Domains.FirstOrDefaultAsync(d => d.Id == req.DomainId, ct);
            if (domain is null) return BadRequest(new { detail = "Domain not found" });
        }

        doc.DomainId = req.DomainId;
        await db.SaveChangesAsync(ct);
        return Ok(new { status = "ok", domainId = doc.DomainId });
    }

    [HttpPut("{documentId}/tags")]
    public async Task<IActionResult> UpdateTags(
        Guid documentId, [FromBody] UpdateTagsRequest req, CancellationToken ct = default)
    {
        var doc = await db.Documents.FirstOrDefaultAsync(d => d.Id == documentId, ct);
        if (doc is null) return NotFound(new { detail = "Document not found" });
        doc.Tags = req.Tags;
        await db.SaveChangesAsync(ct);
        return Ok(new { status = "ok", tags = doc.Tags });
    }

    [HttpGet("/api/tags")]
    public async Task<IActionResult> ListTags(CancellationToken ct = default)
    {
        var allTags = await db.Documents
            .Where(d => d.Tags != null && d.Tags != "")
            .Select(d => d.Tags!)
            .ToListAsync(ct);

        var distinct = allTags
            .SelectMany(t => t.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(t => t)
            .ToList();

        return Ok(new { tags = distinct });
    }
    [HttpPost("{documentId}/reparse")]
    public async Task<IActionResult> Reparse(Guid documentId, CancellationToken ct = default)
    {
        var doc = await db.Documents.FirstOrDefaultAsync(d => d.Id == documentId, ct);
        if (doc is null) return NotFound(new { detail = "Document not found" });

        var content = doc.MarkdownContent;
        if (string.IsNullOrEmpty(content))
            return BadRequest(new { detail = "No stored content to reparse" });

        // Try parsing as legal HTML (content might be plain text from HTML extraction)
        var meta = Services.Parsing.LegalHtmlParser.IsLegalHtml(content)
            ? Services.Parsing.LegalHtmlParser.TryParse(content)
            : null;

        if (meta is null)
            return Ok(new { status = "skipped", detail = "Document is not a recognized legal document" });

        doc.DocumentType = meta.DocumentType;
        doc.DocumentTypeDisplay = meta.DocumentTypeDisplay;
        doc.DocumentNumber = meta.DocumentNumber;
        doc.DocumentTitle = meta.DocumentTitle;
        doc.IssuingAuthority = meta.IssuingAuthority;
        doc.SignedLocation = meta.SignedLocation;
        doc.IssuedDate = meta.IssuedDate;
        doc.LegalBasisJson = meta.LegalBases.Count > 0
            ? System.Text.Json.JsonSerializer.Serialize(meta.LegalBases) : null;
        doc.TerminologyJson = meta.Terminology.Count > 0
            ? System.Text.Json.JsonSerializer.Serialize(meta.Terminology) : null;
        doc.ReferencedDocsJson = meta.ReferencedDocs.Count > 0
            ? System.Text.Json.JsonSerializer.Serialize(meta.ReferencedDocs) : null;

        await db.SaveChangesAsync(ct);
        return Ok(new { status = "ok", documentType = meta.DocumentTypeDisplay, documentNumber = meta.DocumentNumber });
    }
}

public record UpdateTagsRequest(string? Tags);
public record SetDomainRequest(int? DomainId);

