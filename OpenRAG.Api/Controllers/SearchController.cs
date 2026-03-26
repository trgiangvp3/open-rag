using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/search")]
public class SearchController(MlClient ml, LlmClient llm) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Search([FromBody] SearchRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        var results = await ml.SearchAsync(req.Query, req.Collection, req.TopK, req.UseReranker, req.SearchMode, ct);

        if (req.Generate && llm.IsEnabled)
        {
            var generated = await llm.GenerateAsync(req.Query, results, ct: ct);
            return Ok(new SearchResponse(req.Query, results, results.Count, generated?.Answer, generated?.Citations));
        }

        return Ok(new SearchResponse(req.Query, results, results.Count));
    }
}
