using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/collections")]
public class CollectionsController(CollectionService collections) : ControllerBase
{
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
}
