using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services;
using OpenRAG.Api.Services.Chunking;

namespace OpenRAG.Api.Controllers;

[Authorize(Roles = Roles.Admin)]
[ApiController]
[Route("api/collections")]
public class CollectionsController(CollectionService collections) : ControllerBase
{
    [Authorize]
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct = default)
        => Ok(await collections.ListCollectionsAsync(ct));

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CollectionCreateRequest req, CancellationToken ct = default)
    {
        var result = await collections.CreateCollectionAsync(req.Name, req.Description, ct);
        if (result.Status == "error") return Conflict(result);
        return Ok(result);
    }

    [HttpDelete("{name}")]
    public async Task<IActionResult> Delete(string name, CancellationToken ct = default)
    {
        var result = await collections.DeleteCollectionAsync(name, ct);
        if (result.Status == "error") return BadRequest(result);
        return Ok(result);
    }

    [HttpPut("{name}/settings")]
    public async Task<IActionResult> UpdateSettings(string name, [FromBody] CollectionSettingsRequest req, CancellationToken ct = default)
    {
        var result = await collections.UpdateSettingsAsync(name, req, ct);
        if (result.Status == "error") return BadRequest(result);
        return Ok(result);
    }

    [HttpPost("{name}/test-heading-script")]
    public async Task<IActionResult> TestHeadingScript(string name, [FromBody] TestHeadingScriptRequest req, CancellationToken ct = default)
    {
        var col = await collections.GetCollectionAsync(name, ct);
        if (col is null)
            return NotFound(new { status = "error", message = $"Collection '{name}' not found" });

        var chunkSize = req.ChunkSize ?? col.ChunkSize;
        var chunkOverlap = req.ChunkOverlap ?? col.ChunkOverlap;
        var sectionThreshold = req.SectionTokenThreshold ?? col.SectionTokenThreshold;
        var autoDetect = req.AutoDetectHeadings ?? col.AutoDetectHeadings;

        var chunker = new MarkdownChunker(
            chunkSize: chunkSize,
            chunkOverlap: chunkOverlap,
            sectionTokenThreshold: sectionThreshold,
            autoDetectHeadings: autoDetect,
            headingScript: string.IsNullOrWhiteSpace(req.Script) ? null : req.Script);

        var chunks = chunker.Chunk(req.SampleText);

        return Ok(new
        {
            status = "ok",
            chunkCount = chunks.Count,
            chunks = chunks.Select(c => new
            {
                index = c.Index,
                text = c.Text,
                metadata = c.Metadata,
                length = c.Text.Length
            })
        });
    }
}
