using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Dto.Requests;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/chat")]
public class ChatController(ChatService chat) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Chat([FromBody] ChatRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Query))
            return BadRequest(new { detail = "Query is required" });

        try
        {
            var result = await chat.ChatAsync(req, ct);
            return Ok(result);
        }
        catch (KeyNotFoundException ex)
        {
            return NotFound(new { detail = ex.Message });
        }
    }

    [HttpGet("{sessionId:guid}/history")]
    public async Task<IActionResult> GetHistory(Guid sessionId, CancellationToken ct = default)
    {
        var history = await chat.GetHistoryAsync(sessionId, ct);
        if (history is null) return NotFound(new { detail = "Session not found" });
        return Ok(history);
    }

    [HttpDelete("{sessionId:guid}")]
    public async Task<IActionResult> DeleteSession(Guid sessionId, CancellationToken ct = default)
    {
        await chat.DeleteSessionAsync(sessionId, ct);
        return Ok(new StatusResponse("ok", "Session deleted"));
    }
}
