using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/documents")]
public class DocumentsController(DocumentService docs) : ControllerBase
{
    [HttpPost("upload")]
    public async Task<IActionResult> Upload(
        IFormFile file,
        [FromForm] string collection = "documents",
        CancellationToken ct = default)
    {
        if (file is null || file.Length == 0)
            return BadRequest(new { detail = "No file provided" });

        await using var stream = file.OpenReadStream();
        var result = await docs.IngestFileAsync(stream, file.FileName, collection, file.Length, ct);
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
