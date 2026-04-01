using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[Authorize(Roles = Roles.Admin)]
[ApiController]
[Route("api/settings")]
public class SettingsController(AppSettingsService settings) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> GetAll(CancellationToken ct)
    {
        var all = await settings.GetAllAsync(ct);
        // Mask the API key for security
        if (all.ContainsKey("Llm:ApiKey") && all["Llm:ApiKey"].Length > 8)
            all["Llm:ApiKey"] = all["Llm:ApiKey"][..4] + "..." + all["Llm:ApiKey"][^4..];
        return Ok(all);
    }

    [HttpPut]
    public async Task<IActionResult> Update([FromBody] Dictionary<string, string> updates, CancellationToken ct)
    {
        await settings.SetManyAsync(updates, ct);
        return Ok(new { ok = true });
    }
}
