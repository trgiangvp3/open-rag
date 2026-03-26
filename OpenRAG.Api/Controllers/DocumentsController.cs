using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/documents")]
public class DocumentsController(DocumentService docs) : ControllerBase
{
    private const long MaxFileSizeBytes = 100 * 1024 * 1024; // 100 MB
    private const int MaxTextLength = 10 * 1024 * 1024;      // 10 MB
    private static readonly HashSet<string> AllowedExtensions =
        [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md", ".html", ".htm", ".csv"];

    [HttpPost("upload")]
    public async Task<IActionResult> Upload(
        IFormFile file,
        [FromForm] string collection = "documents",
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
        var result = await docs.IngestFileAsync(stream, safeFilename, collection, file.Length, ct);
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
}
