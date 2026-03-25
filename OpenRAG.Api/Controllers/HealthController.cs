using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/health")]
public class HealthController(MlClient ml) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> Health(CancellationToken ct = default)
    {
        var mlOk = await ml.HealthAsync(ct);
        return Ok(new
        {
            status = mlOk ? "ok" : "degraded",
            ml_service = mlOk ? "ok" : "unavailable",
        });
    }
}
